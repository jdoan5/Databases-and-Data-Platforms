// Screenshots of the dashboard for the portfolio card and the README, in the installed Chrome.
// Serves this folder on 127.0.0.1 for the run, then stops the server.
//
//   node screenshot.mjs <out-dir> [--full]
//
// Writes, for light and dark: dashboard-<theme>.png (the top of the page), dashboard-kpis-<theme>.png (KPIs and the
// daily chart), dashboard-daily-<theme>.png (the daily chart and its alerts) and dashboard-attribution-<theme>.png,
// each 1600 x 900 (a 1280 x 720 viewport at 1.25x). --full also writes full-page captures at 1280 and 375 px wide.

import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const out = resolve(process.argv[2] || join(here, 'screenshots'))
const full = process.argv.includes('--full')
const PORT = 5191
mkdirSync(out, { recursive: true })

const server = spawn(process.execPath, [join(here, 'tests/serve.mjs'), String(PORT)], { stdio: ['ignore', 'pipe', 'inherit'] })
await new Promise((ok) => server.stdout.once('data', ok))
const browser = await chromium.launch({ channel: 'chrome' })
const shots = []
try {
  for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.25, colorScheme: theme })
    const page = await ctx.newPage()
    await page.goto(`http://127.0.0.1:${PORT}/`)
    await page.waitForSelector('html[data-ready="true"]')
    await page.evaluate(() => document.fonts.ready)
    const shot = async (name, anchor) => {
      if (anchor) await page.locator(anchor).evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 12))
      else await page.evaluate(() => window.scrollTo(0, 0))
      await page.waitForTimeout(150)
      const file = join(out, `${name}-${theme}.png`)
      await page.screenshot({ path: file })
      shots.push(file)
    }
    await shot('dashboard')
    await shot('dashboard-kpis', '#kpis')
    await shot('dashboard-daily', '#daily')
    await shot('dashboard-attribution', '#attribution')
    await ctx.close()
    if (full) {
      for (const width of [1280, 375]) {
        const c = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1, colorScheme: theme })
        const p = await c.newPage()
        await p.goto(`http://127.0.0.1:${PORT}/`)
        await p.waitForSelector('html[data-ready="true"]')
        const file = join(out, `full-${width}-${theme}.png`)
        await p.screenshot({ path: file, fullPage: true })
        shots.push(file)
        await c.close()
      }
    }
  }
} finally {
  await browser.close()
  server.kill()
}
for (const f of shots) console.log(`wrote ${f}`)
