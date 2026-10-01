// A static file server for the Playwright tests: serves the dashboard folder (or DASHBOARD_ROOT, e.g. the published
// copy) on 127.0.0.1 only. Playwright starts it and stops it after the run.
//
//   node tests/serve.mjs [port]

import { createServer } from 'node:http'
import { createReadStream, statSync } from 'node:fs'
import { dirname, extname, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(process.env.DASHBOARD_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..'))
const port = Number(process.argv[2] || 5190)
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' }

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  let file = normalize(join(root, path))
  if (file !== root && !file.startsWith(root + sep)) { res.writeHead(403).end(); return }
  try {
    if (statSync(file).isDirectory()) file = join(file, 'index.html')
    const size = statSync(file).size
    res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream', 'content-length': size, 'cache-control': 'no-store' })
    createReadStream(file).pipe(res)
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain' }).end('not found')
  }
}).listen(port, '127.0.0.1', () => console.log(`serving ${root} on http://127.0.0.1:${port}`))
