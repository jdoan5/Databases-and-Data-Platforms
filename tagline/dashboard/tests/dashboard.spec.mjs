// The page renders every section from the snapshot, with no console error, no failed or outside request, and no
// horizontal scroll, at 1280 and 375 px (the two projects in playwright.config.mjs).

import { test, expect } from '@playwright/test'

let snap

test.beforeAll(async ({ request }) => {
  const res = await request.get('/data/snapshot.json')
  expect(res.ok()).toBeTruthy()
  snap = await res.json()
})

function watch(page) {
  const problems = []
  page.on('console', (msg) => { if (msg.type() === 'error' || msg.type() === 'warning') problems.push(`console ${msg.type()}: ${msg.text()}`) })
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
  page.on('requestfailed', (req) => problems.push(`request failed: ${req.url()}`))
  page.on('response', (res) => { if (res.status() >= 400) problems.push(`HTTP ${res.status()}: ${res.url()}`) })
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (!['127.0.0.1', 'localhost'].includes(url.hostname) && url.protocol !== 'data:') problems.push(`outside request: ${req.url()}`)
  })
  return problems
}

const int = (n) => new Intl.NumberFormat('en-US').format(n)

test('renders every section from the snapshot, with no console errors', async ({ page }) => {
  const problems = watch(page)
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-ready', 'true')
  await expect(page.locator('#load-error')).toBeHidden()

  // stamp and sources
  await expect(page.locator('#stamp time')).toHaveAttribute('datetime', snap.generated_at)
  await expect(page.locator('#sources .source-card')).toHaveCount(2)
  await expect(page.locator('#sources')).toContainText('synthetic traffic')

  // KPIs: the headline numbers are the snapshot's
  await expect(page.locator('#kpi-groups .tile')).toHaveCount(12)
  await expect(page.locator('#kpi-groups')).toContainText(int(snap.kpis.ga4_sample.sessions))
  await expect(page.locator('#kpi-groups')).toContainText(int(snap.kpis.tagline_site.orders))

  // daily: two lines, one marker per alert day, the backtest's incidents
  const daily = page.locator('#daily-chart svg')
  await expect(daily.locator('path.line-s1')).toHaveCount(2)
  const alertDays = snap.alerts.days.filter((d) => d.source === 'ga4_sample').length
  await expect(daily.locator('path.fill-critical, path.fill-warning')).toHaveCount(alertDays)
  await expect(page.locator('#daily-table tbody tr')).toHaveCount(snap.daily.ga4_sample.length)
  await expect(page.locator('#backtest .incidents button')).toHaveCount(snap.cited.backtest.judged.length)
  await expect(page.locator('#backtest')).toContainText(`${snap.alerts.total} alerts`)

  // funnel: 4 steps x 2 sources
  await expect(page.locator('#funnel-chart path.mark')).toHaveCount(8)

  // attribution: one row per channel, six columns for the chosen channel, the self-referral called out
  await expect(page.locator('#heat-wrap tbody tr')).toHaveCount(snap.attribution.ga4_sample.channels.length)
  await expect(page.locator('#channel-chart path.mark')).toHaveCount(6)
  await expect(page.locator('#channel-title')).toHaveText(snap.cited.self_referral.channel)
  await expect(page.locator('#self-referral .callout')).toContainText('self-referral')
  await expect(page.locator('#site-attribution tbody tr')).toHaveCount(snap.attribution.tagline_site.channels.length)

  // tag health: three layers, a stacked bar per source, the by-kind table
  await expect(page.locator('#layers .layer')).toHaveCount(3)
  const segments = ['ga4_sample', 'tagline_site'].reduce((t, s) => t + ['pass', 'expected', 'violation'].filter((k) => snap.tag_health.by_source[s][k] > 0).length, 0)
  await expect(page.locator('#health-chart path.mark')).toHaveCount(segments)
  await expect(page.locator('#health-kinds tbody tr')).not.toHaveCount(0)

  // campaigns and cost
  await expect(page.locator('#campaign-table tbody tr')).toHaveCount(snap.campaigns.tagline_site.rows.length)
  await expect(page.locator('#wall-chart path.mark')).toHaveCount(snap.cited.pipeline_runs.runs.length)
  await expect(page.locator('#price-chart path.mark')).toHaveCount(snap.cited.pipeline_runs.runs.length)
  await expect(page.locator('#cost-detail a')).not.toHaveCount(0)
  await expect(page.locator('#footer li')).toHaveCount(snap.tables.length)

  // every cited figure links to its document
  for (const href of await page.locator('.source-link a, #cost-detail td a').evaluateAll((as) => as.map((a) => a.href))) {
    expect(href).toMatch(/^https:\/\/github\.com\/jdoan5\/Databases-and-Data-Platforms\/blob\/main\/tagline\//)
  }

  // layout: no horizontal page scroll, nothing wider than the viewport
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  expect(problems).toEqual([])
})

test('keyboard and pointer reach the values; selections and the theme toggle work', async ({ page }) => {
  const problems = watch(page)
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-ready', 'true')

  // the daily chart: focus, End, read the last day
  const chart = page.locator('#daily-chart')
  await chart.focus()
  await page.keyboard.press('End')
  const last = snap.daily.ga4_sample.at(-1)
  await expect(chart.locator('.tooltip')).toBeVisible()
  await expect(chart.locator('.tooltip')).toContainText(int(last.sessions))
  await expect(page.locator('#daily-live')).toContainText(int(last.sessions))

  // a funnel bar by keyboard focus
  const bar = page.locator('#funnel-chart path.mark').first()
  await bar.focus()
  await expect(page.locator('#funnel-chart .tooltip')).toBeVisible()

  // pick another channel in the heatmap
  const other = snap.attribution.ga4_sample.channels.find((c) => !c.other && c.channel !== snap.cited.self_referral.channel)
  await page.getByRole('button', { name: other.channel, exact: true }).click()
  await expect(page.locator('#channel-title')).toHaveText(other.channel)

  // an incident shades its days
  await page.locator('#backtest .incidents button').first().click()
  await expect(page.locator('#daily-chart rect.band')).toHaveCount(1)

  // theme: Auto -> Light -> Dark, and the surfaces change
  const toggle = page.locator('#theme-toggle')
  await expect(page.locator('html')).toHaveAttribute('data-theme-mode', 'auto')
  await toggle.click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  const lightBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  await toggle.click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(darkBg).not.toBe(lightBg)
  expect(await page.evaluate(() => localStorage.getItem('theme-mode'))).toBe('dark')

  expect(problems).toEqual([])
})

test('follows the system dark mode on Auto', async ({ page }) => {
  const problems = watch(page)
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-ready', 'true')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const surface = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--surface').trim())
  expect(surface).toBe('#162126')
  expect(problems).toEqual([])
})

test('accessible structure: landmarks, one h1, labelled marks and tables', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-ready', 'true')
  await expect(page.locator('h1')).toHaveCount(1)
  await expect(page.locator('main')).toHaveCount(1)
  const unlabeled = await page.locator('[role="img"]').evaluateAll((els) => els.filter((e) => !(e.getAttribute('aria-label') || '').trim()).length)
  expect(unlabeled).toBe(0)
  const uncaptioned = await page.locator('table').evaluateAll((ts) => ts.filter((t) => !t.querySelector('caption') && !t.closest('details')).length)
  expect(uncaptioned).toBe(0)
  const sections = await page.locator('main section.block').evaluateAll((ss) => ss.map((s) => s.querySelector('h2')?.textContent || ''))
  expect(sections.every((t) => t.length > 0)).toBeTruthy()
})
