/**
 * Starting and stopping the site for a run.
 *
 * The measurement id is baked in at start: `preview` (the default) runs `vite build`
 * into simulator/.build with VITE_GA4_MEASUREMENT_ID set, then serves it with
 * `vite preview`: the production bundle real visitors would get, and fast to load in
 * dozens of fresh browser contexts. `dev` runs the Vite dev server instead (no build,
 * StrictMode on, slower per page). An environment variable beats the site's .env files
 * in Vite, so a measurement id in site/.env.local cannot leak into a dry run.
 *
 * The build never touches site/dist, which may hold the owner's own build.
 */
import { spawn } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'

export const SITE_DIR = fileURLToPath(new URL('../../site/', import.meta.url))
export const BUILD_DIR = fileURLToPath(new URL('../.build/', import.meta.url))
const VITE = fileURLToPath(new URL('../../site/node_modules/vite/bin/vite.js', import.meta.url))

function portFree(port) {
  return new Promise((resolve) => {
    const srv = createServer()
    srv.once('error', () => resolve(false))
    srv.listen(port, 'localhost', () => srv.close(() => resolve(true)))
  })
}

async function freePort(preferred) {
  if (await portFree(preferred)) return preferred
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.once('error', reject)
    srv.listen(0, 'localhost', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

function run(args, env, { quiet }) {
  return spawn(process.execPath, [VITE, ...args], {
    cwd: SITE_DIR,
    env: { ...process.env, ...env },
    stdio: quiet ? ['ignore', 'pipe', 'pipe'] : ['ignore', 'inherit', 'inherit'],
  })
}

function waitForExit(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) return resolve(child.exitCode)
    child.once('exit', (code) => resolve(code))
  })
}

async function waitForHttp(url, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`the site server exited with code ${child.exitCode} before it was ready`)
    try {
      const res = await fetch(url)
      if (res.ok) return
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`the site did not answer at ${url} within ${timeoutMs / 1000}s`)
}

/**
 * Starts the site with the given measurement id and resolves to { baseURL, stop }.
 * `stop` is idempotent and is also run on process exit.
 */
export async function startSite({ measurementId, server = 'preview', port: preferred = 5190, log = () => {} }) {
  if (!existsSync(VITE)) throw new Error('Vite is not installed in tagline/site: run `npm ci` there first.')
  const env = { VITE_GA4_MEASUREMENT_ID: measurementId }
  const collected = []
  const capture = (child) => {
    child.stdout?.on('data', (d) => collected.push(String(d)))
    child.stderr?.on('data', (d) => collected.push(String(d)))
  }

  if (server === 'preview') {
    log(`building the site with VITE_GA4_MEASUREMENT_ID=${measurementId} into simulator/.build`)
    rmSync(BUILD_DIR, { recursive: true, force: true })
    const build = run(['build', '--outDir', BUILD_DIR, '--emptyOutDir', '--logLevel', 'warn'], env, { quiet: true })
    capture(build)
    const code = await waitForExit(build)
    if (code !== 0) throw new Error(`vite build failed (exit ${code}):\n${collected.join('')}`)
  } else if (server !== 'dev') {
    throw new Error(`unknown server "${server}" (preview or dev)`)
  }

  const port = await freePort(preferred)
  const args =
    server === 'preview'
      ? ['preview', '--outDir', BUILD_DIR, '--port', String(port), '--strictPort']
      : ['--port', String(port), '--strictPort']
  const child = run(args, env, { quiet: true })
  capture(child)
  const baseURL = `http://localhost:${port}`

  let stopped = false
  const stop = async () => {
    if (stopped) return
    stopped = true
    process.off('exit', killNow)
    if (child.exitCode === null) {
      child.kill('SIGTERM')
      const timer = setTimeout(() => child.kill('SIGKILL'), 3000)
      await waitForExit(child)
      clearTimeout(timer)
    }
  }
  const killNow = () => child.exitCode === null && child.kill('SIGKILL')
  process.on('exit', killNow)

  try {
    await waitForHttp(baseURL, child, 60_000)
  } catch (err) {
    await stop()
    throw new Error(`${err.message}\n${collected.join('')}`)
  }
  log(`site (${server}) at ${baseURL}`)
  return { baseURL, stop, output: () => collected.join('') }
}
