/**
 * Executing a plan in real Chrome (Playwright, channel 'chrome': the Chrome already
 * installed, no browser download).
 *
 * Every device is a fresh browser context, so it has its own cookies and storage and
 * gtag.js gives it its own client id: to GA4 it is a different device. People run in
 * parallel (--concurrency); one person's devices run one after another, so a sign_up
 * always comes before the logins on that person's other devices.
 *
 * Every request to a Google host goes through one route handler per context. gtag.js
 * itself is let through (in the dry run it is fetched once and served from memory to
 * later contexts), so the hits are the ones gtag.js really forms. Each /g/collect
 * request is recorded and parsed; then the dry run aborts it and live mode lets it go.
 */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'
import { GOOGLE_HOST, findPii, isCollect, isGtagScript, parseCollect } from './hits.js'
import { PRODUCTS, buildPlan, displayName, planStats } from './plan.js'
import { startSite } from './server.js'
import { summarise } from './summary.js'

export const DRY_RUN_ID = 'G-DRYRUN0000'

/**
 * A second lock on the dry run, under the route handler: these hosts do not resolve in
 * the dry-run browser, so even a request the handler never saw (say, a beacon sent as
 * a page closes) cannot reach a Google collection endpoint. googletagmanager.com is
 * left resolvable because gtag.js is loaded from it.
 */
const DRY_RUN_HOSTS = ['google-analytics.com', '*.google-analytics.com', 'analytics.google.com', '*.analytics.google.com', '*.doubleclick.net', 'www.google.com']
const DRY_RUN_RESOLVER_RULES = DRY_RUN_HOSTS.map((h) => `MAP ${h} ~NOTFOUND`).join(', ')

// ---------------------------------------------------------------------------------
// The stand-in account directory

/**
 * The site's stand-in account service (site/src/auth/accounts.ts) keeps its directory
 * in the browser: { sha256(prefix + email): accountId } under one localStorage key. A
 * real store's account service returns the same id for a customer on every device;
 * this one would issue a new random id in every fresh browser. So before a device
 * signs in, the simulator writes that person's entry into the new context's storage,
 * playing the role of the backend, and the site then finds the same account id on
 * every device of the person. The key and prefix are read from the site's source so
 * the two cannot drift apart.
 */
const accountsSource = readFileSync(new URL('../../site/src/auth/accounts.ts', import.meta.url), 'utf8')
const ACCOUNTS_KEY = accountsSource.match(/const KEY = '([^']+)'/)?.[1]
const LOOKUP_PREFIX = accountsSource.match(/const LOOKUP_PREFIX = '([^']+)'/)?.[1]
if (!ACCOUNTS_KEY || !LOOKUP_PREFIX) throw new Error('could not read KEY / LOOKUP_PREFIX from site/src/auth/accounts.ts')

export function accountDirectory(email, accountId) {
  const key = createHash('sha256').update(LOOKUP_PREFIX + email.trim().toLowerCase()).digest('hex')
  return { [key]: accountId }
}

// ---------------------------------------------------------------------------------
// Steps: each one is what a visitor would do with the mouse and keyboard

const byId = new Map(PRODUCTS.map((p) => [p.item_id, p]))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const h1 = (page, name) => page.getByRole('heading', { level: 1, name, exact: true })

const STEPS = {
  async land(page, step, { baseURL }) {
    await page.goto(new URL(step.url, baseURL).href, { referer: step.referrer, waitUntil: 'load' })
    await page.locator('main h1').first().waitFor()
  },
  async consent(page, step) {
    const banner = page.getByRole('region', { name: 'Cookies' })
    await banner.getByRole('button', { name: step.choice === 'accept' ? 'Accept' : 'Reject' }).click()
    await banner.waitFor({ state: 'detached' })
  },
  async category(page, step) {
    const nav = page.getByRole('navigation', { name: 'Categories' })
    if ((await nav.count()) === 0) {
      await page.locator('a.brand').click()
      await h1(page, 'All products').waitFor()
    }
    await nav.getByRole('link', { name: step.name, exact: true }).click()
    await page.waitForURL((u) => u.pathname === `/category/${step.slug}`)
    await h1(page, step.name).waitFor()
  },
  async search(page, step) {
    const before = page.url()
    const box = page.locator('#site-search')
    await box.fill(step.term)
    await box.press('Enter')
    await page.waitForURL((u) => u.pathname === '/search' && u.href !== before)
    await page.locator('main h1').filter({ hasText: 'Results for' }).waitFor()
  },
  async product(page, step) {
    await page.locator(`main a[data-item-id="${step.itemId}"]`).click()
    await page.waitForURL((u) => u.pathname === `/product/${step.itemId}`)
    await h1(page, byId.get(step.itemId).item_name).waitFor()
  },
  async add(page, step) {
    if (step.quantity > 1) await page.getByLabel('Quantity').selectOption(String(step.quantity))
    await page.getByRole('button', { name: 'Add to cart' }).click()
    await page.getByRole('status').filter({ hasText: `Added ${step.quantity}` }).waitFor()
  },
  async cart(page) {
    await page.locator('a.cart-link').click()
    await page.waitForURL((u) => u.pathname === '/cart')
    await h1(page, 'Cart').waitFor()
  },
  async cart_less(page, step) {
    const name = displayName(byId.get(step.itemId))
    const qty = page.getByRole('group', { name: `Quantity of ${name}` }).locator('span[aria-live]')
    const before = await qty.textContent()
    await page.getByRole('button', { name: `One fewer ${name}` }).click()
    if (before !== '1') await page.waitForFunction(([el, b]) => el?.textContent !== b, [await qty.elementHandle(), before])
  },
  async checkout(page) {
    await page.getByRole('link', { name: 'Checkout' }).click()
    await page.waitForURL((u) => u.pathname === '/checkout')
    await h1(page, 'Checkout').waitFor()
  },
  async shipping(page, step) {
    await page.locator('#shipping').selectOption(step.tier)
  },
  async payment(page, step) {
    await page.locator('#payment').selectOption(step.type)
  },
  async place_order(page, _step, _env, result) {
    await page.getByRole('button', { name: 'Place order' }).click()
    await page.waitForURL((u) => u.pathname.startsWith('/order/'))
    await page.locator('main h1').first().waitFor()
    result.transactionIds.push(decodeURIComponent(new URL(page.url()).pathname.split('/')[2]))
  },
  async sign_in(page, step, _env, result) {
    await page.locator('header').getByRole('link', { name: 'Sign in' }).click()
    await h1(page, 'Sign in').waitFor()
    await page.locator('#email').fill(step.email)
    await page.getByRole('button', { name: step.mode === 'sign_up' ? 'Create account' : 'Sign in', exact: true }).click()
    await h1(page, 'Signed in').waitFor()
    result.userId = (await page.getByTestId('user-id').textContent())?.trim() ?? null
    await page.getByRole('link', { name: 'Back to the store' }).click()
    await h1(page, 'All products').waitFor()
  },
}

/** The event names the site pushed to window.dataLayer (its own pushes, not gtag commands). */
const readDataLayerEvents = (page) =>
  page.evaluate(() =>
    (window.dataLayer ?? [])
      .filter((e) => e && typeof e === 'object' && Object.prototype.toString.call(e) !== '[object Arguments]')
      .map((e) => e.event)
      .filter((name) => typeof name === 'string' && !name.startsWith('gtm.')),
  )

// ---------------------------------------------------------------------------------
// Running

async function runDevice(browser, env, person, device) {
  const { baseURL, dryRun, pace, record, outDir } = env
  const result = {
    deviceId: device.deviceId,
    personId: person.personId,
    channel: device.channel,
    consent: device.consent,
    signIn: device.signIn,
    depth: device.depth,
    userId: null,
    transactionIds: [],
    dataLayerEvents: [],
    stepsDone: 0,
    steps: device.steps.length,
    errors: [],
    startedAt: new Date().toISOString(),
  }
  const started = Date.now()
  const storageState = device.signIn
    ? { cookies: [], origins: [{ origin: baseURL, localStorage: [{ name: ACCOUNTS_KEY, value: JSON.stringify(accountDirectory(person.email, person.accountId)) }] }] }
    : undefined
  const context = await browser.newContext({ viewport: { width: device.window.width, height: device.window.height }, storageState })
  context.setDefaultTimeout(15_000)
  context.setDefaultNavigationTimeout(30_000)
  await context.route((url) => GOOGLE_HOST.test(url.hostname), (route, request) => record(route, request, device, person).catch(() => {}))
  if (dryRun) {
    context.on('requestfinished', (req) => {
      const url = new URL(req.url())
      // A fulfilled (answered-locally) request also "finishes"; only one the route
      // handler never saw would have gone over the network.
      if (GOOGLE_HOST.test(url.hostname) && !isGtagScript(url) && !env.handled.has(req)) env.leaks.push({ device: device.deviceId, url: req.url() })
    })
  }
  const page = await context.newPage()
  page.on('pageerror', (e) => result.errors.push(`page error: ${e.message}`))
  let current = null
  try {
    for (const step of device.steps) {
      current = step
      await STEPS[step.do](page, step, env, result)
      result.stepsDone++
      await sleep(Math.round(step.thinkMs * pace))
    }
    // gtag.js batches events and sends a batch about five seconds after the first one
    // was queued; in these automated contexts it did not flush on leaving the page
    // (tried: navigating away, closing with beforeunload, hiding the tab). So the
    // visitor lingers on the last page until every event the site pushed has gone out,
    // up to 12 s, as a real visitor reading the confirmation page would.
    const deadline = Date.now() + 12_000
    do {
      result.dataLayerEvents = await readDataLayerEvents(page)
      if (env.sentCount(device.deviceId) >= result.dataLayerEvents.length) break
      await sleep(250)
    } while (Date.now() < deadline)
    result.lingerMs = 12_000 - Math.max(0, deadline - Date.now())
    await page.goto('about:blank')
    await sleep(300)
  } catch (err) {
    result.errors.push(`${current?.do ?? 'setup'}: ${String(err.message).split('\n')[0]}`)
    try {
      mkdirSync(join(outDir, 'errors'), { recursive: true })
      await page.screenshot({ path: join(outDir, 'errors', `${device.deviceId}.png`), fullPage: true })
    } catch {
      // the screenshot is a nicety
    }
  } finally {
    await context.close()
  }
  result.seconds = Math.round((Date.now() - started) / 100) / 10
  return result
}

async function pool(items, size, worker) {
  const queue = [...items]
  const runners = Array.from({ length: Math.min(size, queue.length) }, async () => {
    while (queue.length) await worker(queue.shift())
  })
  await Promise.all(runners)
}

/**
 * Runs a whole simulation and returns the summary (see summary.js). Writes plan.json,
 * hits.ndjson, devices.ndjson, requests.ndjson and summary.json into outDir.
 */
export async function runSimulation(options) {
  const {
    people,
    seed,
    population,
    concurrency = 4,
    pace = 1,
    live = false,
    measurementId,
    headless = !live,
    server = 'preview',
    abortCollect = false,
    outDir,
    log = console.log,
  } = options
  const dryRun = !live
  const id = dryRun ? DRY_RUN_ID : measurementId
  if (!dryRun && !/^G-[A-Z0-9]{4,}$/.test(id ?? '')) throw new Error('live mode needs a GA4 measurement id like G-XXXXXXXXXX')

  const plan = buildPlan({ people, seed, population })
  mkdirSync(outDir, { recursive: true })
  writeFileSync(join(outDir, 'plan.json'), JSON.stringify(plan, null, 2))
  const stats = planStats(plan)
  log(`plan: ${stats.people} people, ${stats.devices} devices, ${stats.purchases} planned purchases, ${stats.crossDevicePeople} people signing in on 2+ devices (seed "${plan.seed}", population "${plan.population}")`)

  const requests = [] // every intercepted Google request except the gtag.js script
  const hits = []
  const emails = plan.people.map((p) => p.email)
  let gtagScript = null

  const handled = new WeakSet()
  const record = async (route, request, device, person) => {
    handled.add(request)
    const url = new URL(request.url())
    if (isGtagScript(url)) {
      if (!dryRun) return route.continue()
      // One real download of gtag.js per run; later contexts get the same bytes.
      gtagScript ??= route.fetch().then(async (res) => ({
        status: res.status(),
        contentType: res.headers()['content-type'] ?? 'application/javascript',
        body: await res.body(),
      }))
      try {
        const s = await gtagScript
        return route.fulfill({ status: s.status, contentType: s.contentType, body: s.body })
      } catch (err) {
        gtagScript = null
        return route.abort('failed')
      }
    }
    const body = request.postData() ?? ''
    const entry = {
      i: requests.length,
      t: Date.now(),
      device_id: device.deviceId,
      person_id: person.personId,
      method: request.method(),
      resource_type: request.resourceType(),
      host: url.hostname,
      path: url.pathname,
      collect: isCollect(url),
      action: !dryRun ? 'sent' : abortCollect || !isCollect(url) ? 'aborted' : 'answered locally (204)',
      pii: findPii(`${request.url()}\n${body}`, emails),
      url: request.url(),
      body,
    }
    requests.push(entry)
    if (entry.collect) {
      parseCollect(request.url(), body).forEach((hit, line) => {
        hits.push({
          i: hits.length,
          t: new Date(entry.t).toISOString(),
          mode: dryRun ? 'dry-run' : 'live',
          device_id: device.deviceId,
          person_id: person.personId,
          planned_channel: device.channel,
          request: entry.i,
          line,
          host: entry.host,
          transport: `${entry.method} ${entry.resource_type}`,
          ...hit,
        })
      })
    }
    if (!dryRun) return route.continue()
    // Answered locally, as Google's endpoint answers (204, no body), so gtag.js carries
    // on as if delivered. An aborted hit is not what gtag.js does in real life: it
    // retries the same hit at www.google.com/g/collect and holds back later events
    // (README, "Dry run: why hits are answered, not aborted"). --abort-collect keeps
    // the abort, to see that for yourself.
    if (abortCollect || !entry.collect) return route.abort('blockedbyclient')
    return route.fulfill({ status: 204, body: '' })
  }

  const site = await startSite({ measurementId: id, server, log })
  let browser
  const results = []
  const leaks = []
  const started = Date.now()
  try {
    browser = await chromium.launch({
      channel: 'chrome',
      headless,
      args: dryRun ? [`--host-resolver-rules=${DRY_RUN_RESOLVER_RULES}`] : [],
    })
    log(`Chrome ${browser.version()} ${headless ? 'headless' : 'headed'}, ${concurrency} at a time, pace ×${pace}`)
    // Hits for a device that the site pushed itself (gtag.js's own user_engagement aside).
    const sentCount = (deviceId) => hits.reduce((n, h) => n + (h.device_id === deviceId && h.en !== 'user_engagement' ? 1 : 0), 0)
    const env = { baseURL: site.baseURL, dryRun, pace, record, outDir, leaks, handled, sentCount }
    await pool(plan.people, concurrency, async (person) => {
      for (const device of person.devices) {
        if (device.gapBeforeMs) await sleep(Math.round(device.gapBeforeMs * pace))
        const r = await runDevice(browser, env, person, device)
        results.push(r)
        const n = hits.filter((h) => h.device_id === r.deviceId).length
        log(
          `  ${r.deviceId.padEnd(8)} ${r.channel.padEnd(14)} ${r.depth.padEnd(16)} ${r.consent.padEnd(6)} ${String(r.signIn ?? '-').padEnd(7)} ` +
            `${String(r.stepsDone).padStart(2)}/${r.steps} steps ${String(n).padStart(3)} hits ${r.seconds}s ${r.errors.length ? `FAILED: ${r.errors[0]}` : 'ok'}`,
        )
      }
    })
  } finally {
    await browser?.close()
    await site.stop()
  }

  const order = new Map(plan.people.flatMap((p) => p.devices.map((d, i) => [d.deviceId, `${p.personId}-${i}`])))
  results.sort((a, b) => order.get(a.deviceId).localeCompare(order.get(b.deviceId)))
  const summary = summarise({ plan, devices: results, hits, requests, leaks, mode: dryRun ? 'dry-run' : 'live', measurementId: id, headless, seconds: (Date.now() - started) / 1000 })

  const ndjson = (rows) => rows.map((r) => JSON.stringify(r)).join('\n') + (rows.length ? '\n' : '')
  writeFileSync(join(outDir, 'hits.ndjson'), ndjson(hits))
  writeFileSync(join(outDir, 'requests.ndjson'), ndjson(requests))
  writeFileSync(join(outDir, 'devices.ndjson'), ndjson(results))
  writeFileSync(join(outDir, 'summary.json'), JSON.stringify(summary, null, 2))
  return summary
}
