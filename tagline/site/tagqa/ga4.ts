/**
 * The GA4 hit layer: what gtag.js actually sends for the site's pushes.
 *
 * The site is built with a fake measurement id, so it loads gtag.js and forwards every
 * event (site/src/tagging/ga4.ts). Every request to a Google host goes through one
 * route handler: gtag.js itself is downloaded once per worker (up to three tries, so a
 * network blip does not fail a journey) and served from memory;
 * each /g/collect hit is recorded, parsed (batched bodies too) and answered locally
 * with a 204, as Google's endpoint answers, so gtag.js carries on as if delivered;
 * anything else is aborted. The browser also cannot resolve Google's collection hosts
 * (RESOLVER_RULES), so a request the handler never saw could not reach one either.
 * Nothing is sent to Google. The parser is the simulator's (simulator/src/hits.js).
 *
 * checkHits() then holds every hit against the dataLayer push it came from.
 */
import type { BrowserContext, Page, Request } from '@playwright/test'
import { GOOGLE_HOST, isCollect, isGtagScript, parseCollect } from '../../simulator/src/hits.js'
import { align, paramDiff } from './diff'
import { eventName, isConsentDefault, isConsentUpdate, isGoogleInternal, isRecord, type Recording } from './entries'
import { piiKinds, type Violation } from './rules'

export const MEASUREMENT_ID = 'G-TAGQA0000'

const UNRESOLVABLE = ['google-analytics.com', '*.google-analytics.com', 'analytics.google.com', '*.analytics.google.com', '*.doubleclick.net', 'www.google.com']
/** Chrome flag: Google's collection hosts do not resolve. googletagmanager.com does, for gtag.js. */
export const RESOLVER_RULES = `--host-resolver-rules=${UNRESOLVABLE.map((h) => `MAP ${h} ~NOTFOUND`).join(', ')}`

/** One event of a /g/collect request, as simulator/src/hits.js parses it. */
export interface Hit {
  en: string
  tid?: string
  uid?: string
  dl?: string
  dt?: string
  dr?: string
  gcs?: string
  currency?: string
  page_load_id?: string
  ep: Record<string, string>
  epn: Record<string, number>
  items: Record<string, unknown>[]
}

interface CollectRequest {
  url: string
  body: string
}

/** The contract's event names: hits with any other name are gtag.js's own (user_engagement). */
export type SiteEventNames = ReadonlySet<string>

let gtagScript: Promise<{ status: number; contentType: string; body: Buffer }> | null = null

export const GTAG_TRIES = 3

/**
 * `attempt()` until it resolves, at most `tries` times, waiting 1 s, then 2 s … between
 * tries; rejects with the last error. For the gtag.js download: one timeout on the way to
 * www.googletagmanager.com should not fail a journey that has nothing wrong with its tags.
 */
export async function withRetries<T>(
  attempt: () => Promise<T>,
  tries = GTAG_TRIES,
  delayMs = 1000,
  sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
): Promise<T> {
  for (let n = 1; ; n++) {
    try {
      return await attempt()
    } catch (err) {
      if (n >= tries) throw err
      await sleep(delayMs * 2 ** (n - 1))
    }
  }
}

export class Ga4Capture {
  readonly hits: Hit[] = []
  readonly requests: CollectRequest[] = []
  /** Google requests other than gtag.js and /g/collect, aborted. */
  readonly aborted: string[] = []
  /** Google requests that completed without going through the handler. Must stay empty. */
  readonly leaks: string[] = []
  scriptError: string | null = null
  private readonly handled = new WeakSet<Request>()
  private readonly siteEvents: SiteEventNames

  constructor(siteEvents: SiteEventNames) {
    this.siteEvents = siteEvents
  }

  async install(context: BrowserContext): Promise<void> {
    await context.route(
      (url) => GOOGLE_HOST.test(url.hostname),
      async (route, request) => {
        this.handled.add(request)
        const url = new URL(request.url())
        if (isGtagScript(url)) {
          gtagScript ??= withRetries(async () => {
            const res = await route.fetch({ timeout: 20_000 })
            // A server error is worth another try too; anything else (200, a 4xx) is the answer.
            if (res.status() >= 500) throw new Error(`HTTP ${res.status()}`)
            return { status: res.status(), contentType: res.headers()['content-type'] ?? 'application/javascript', body: await res.body() }
          })
          try {
            const script = await gtagScript
            if (script.status !== 200) this.scriptError = `gtag.js answered HTTP ${script.status}`
            return await route.fulfill({ status: script.status, contentType: script.contentType, body: script.body })
          } catch (err) {
            gtagScript = null
            this.scriptError = `gtag.js could not be downloaded from www.googletagmanager.com in ${GTAG_TRIES} tries: ${String(err).split('\n')[0]}`
            return await route.abort('failed')
          }
        }
        if (isCollect(url)) {
          const body = request.postData() ?? ''
          this.requests.push({ url: request.url(), body })
          this.hits.push(...(parseCollect(request.url(), body) as unknown as Hit[]))
          return await route.fulfill({ status: 204, body: '' })
        }
        this.aborted.push(`${url.origin}${url.pathname}`)
        return await route.abort('blockedbyclient')
      },
    )
    context.on('requestfinished', (req) => {
      const url = new URL(req.url())
      if (GOOGLE_HOST.test(url.hostname) && !this.handled.has(req)) this.leaks.push(req.url())
    })
  }

  siteHits(): Hit[] {
    return this.hits.filter((h) => this.siteEvents.has(h.en))
  }

  ownHits(): Record<string, number> {
    const counts: Record<string, number> = {}
    for (const h of this.hits) if (!this.siteEvents.has(h.en)) counts[h.en] = (counts[h.en] ?? 0) + 1
    return counts
  }

  /**
   * gtag.js batches: it sends queued events a few seconds after the first one, and in
   * automated Chrome it did not flush them on leaving the page (simulator/src/run.js).
   * So before a reload, and at the end, wait until there is a hit for every event the
   * site pushed. Returns when caught up or at the deadline; checkHits reports the gap.
   */
  async flush(page: Page, siteEventCount: number, timeoutMs = 20_000): Promise<void> {
    const deadline = Date.now() + timeoutMs
    while (this.siteHits().length < siteEventCount && !this.scriptError && Date.now() < deadline) {
      await page.waitForTimeout(200)
    }
  }
}

/** Events the site pushed (not gtm.* ones), over every page load. */
export const siteEventCount = (rec: Recording): number =>
  rec.loads.reduce((n, l) => n + l.pushes.filter((p) => eventName(p) !== undefined && !isGoogleInternal(p)).length, 0)

// ---- the translation ---------------------------------------------------------------

type Rec = Record<string, unknown>

/**
 * What gtag.js should send for one dataLayer event, the documented way: the ecommerce
 * object is flattened into the params (site/src/tagging/ga4.ts); page_location,
 * page_title and page_referrer become dl, dt and dr; currency becomes cu; items become
 * pr1…prN; every other string param is ep.<name> and every number epn.<name>.
 */
export function expectedHit(event: Rec): { ep: Rec; epn: Rec; items: unknown[]; currency?: unknown; dl?: unknown; dt?: unknown; dr?: unknown } {
  const { event: _name, ecommerce, ...rest } = event
  const params: Rec = { ...rest, ...(isRecord(ecommerce) ? ecommerce : {}) }
  const out: ReturnType<typeof expectedHit> = { ep: {}, epn: {}, items: [] }
  for (const [k, v] of Object.entries(params)) {
    if (k.startsWith('gtm.')) continue
    if (k === 'page_location') out.dl = v
    else if (k === 'page_title') out.dt = v
    else if (k === 'page_referrer') out.dr = v
    else if (k === 'currency') out.currency = v
    else if (k === 'items') out.items = Array.isArray(v) ? v : []
    else if (typeof v === 'number') out.epn[k] = v
    else out.ep[k] = v
  }
  return out
}

const gcsFor = (consent: { ad: string; analytics: string }) => `G1${consent.ad === 'granted' ? 1 : 0}${consent.analytics === 'granted' ? 1 : 0}`

export interface HitContext {
  measurementId: string
  typed?: readonly string[]
}

/**
 * Every hit against the push it came from, page load by page load:
 *   ga4-sequence  one hit per site event, same names, same order
 *   ga4-params    ep.* / epn.* / cu / items exactly the event's params; dl, dt (and dr on
 *                 page_view) the page's cleaned page_location, page_title, page_referrer
 *   ga4-user-id   uid only while signed in: the id of the last { user_id } push; absent before
 *                 any sign-in, absent or empty after a sign-out
 *   ga4-consent   gcs the consent state of the last consent command before the event
 *   ga4-tid       the measurement id the build was given
 *   ga4-pii       no request carries anything email-shaped or the journey's typed text
 *   ga4-leak      no Google request completed without the handler answering it
 *   ga4-script    gtag.js was downloaded
 */
export function checkHits(rec: Recording, capture: Pick<Ga4Capture, 'siteHits' | 'requests' | 'leaks' | 'scriptError'>, ctx: HitContext): Violation[] {
  const out: Violation[] = []
  if (capture.scriptError) out.push({ rule: 'ga4-script', where: 'journey', message: capture.scriptError })
  for (const leak of capture.leaks) out.push({ rule: 'ga4-leak', where: 'journey', message: `a Google request completed without the handler: ${leak}` })
  capture.requests.forEach((r, i) => {
    // Parameter by parameter (the query string and every line of a batched body), then
    // the request as a whole, in case something sits outside a parameter value.
    const url = new URL(r.url)
    const params = [...url.searchParams, ...r.body.split(/\r?\n/).flatMap((line) => [...new URLSearchParams(line)])]
    const found = params.flatMap(([k, v]) => {
      const kinds = piiKinds(v, ctx.typed)
      return kinds.length ? [`${k} ${JSON.stringify(v.slice(0, 120))}: ${kinds.join('; ')}`] : []
    })
    const whole = found.length ? [] : piiKinds(`${r.url}\n${r.body}`, ctx.typed)
    if (whole.length) found.push(`the request: ${whole.join('; ')}`)
    for (const f of found) out.push({ rule: 'ga4-pii', where: `request ${i + 1}`, message: f })
  })

  // Hits grouped by gtag.js's page load id (_p), in the order the loads first sent one.
  const byLoad = new Map<string, Hit[]>()
  for (const h of capture.siteHits()) {
    const key = h.page_load_id ?? '?'
    if (!byLoad.has(key)) byLoad.set(key, [])
    byLoad.get(key)!.push(h)
  }
  const hitLoads = [...byLoad.values()]
  if (hitLoads.length !== rec.loads.length) {
    out.push({ rule: 'ga4-sequence', where: 'journey', message: `hits came from ${hitLoads.length} page load(s), the dataLayer has ${rec.loads.length}` })
  }

  // Forwarding commands (gtag set/event arrays) and gtm.* events match no branch below.
  rec.loads.forEach((load, l) => {
    const hits = hitLoads[l] ?? []
    // Walk the pushes, keeping the state gtag.js keeps: consent, user_id, the page.
    const consent = { ad: 'denied', analytics: 'denied' }
    // undefined: no user_id pushed in this page load yet; null: cleared by a sign-out.
    let uid: string | null | undefined
    let page: Rec = {}
    const events: { i: number; e: Rec; consent: typeof consent; uid: string | null | undefined; page: Rec }[] = []
    load.pushes.forEach((p, i) => {
      if ((isConsentDefault(p) || isConsentUpdate(p)) && Array.isArray(p) && isRecord(p[2])) {
        if (typeof p[2].ad_storage === 'string') consent.ad = p[2].ad_storage
        if (typeof p[2].analytics_storage === 'string') consent.analytics = p[2].analytics_storage
      } else if (isRecord(p) && 'user_id' in p && !('event' in p)) {
        uid = typeof p.user_id === 'string' ? p.user_id : null
      } else if (isRecord(p) && eventName(p) !== undefined && !isGoogleInternal(p)) {
        if (p.event === 'page_view') page = p
        events.push({ i, e: p, consent: { ...consent }, uid, page })
      }
    })

    const names = events.map((x) => String(x.e.event))
    const sent = hits.map((h) => h.en)
    if (JSON.stringify(names) !== JSON.stringify(sent)) {
      out.push({
        rule: 'ga4-sequence',
        where: `load ${l + 1}`,
        message: `${sent.length} hit(s) for ${names.length} event(s)\n      dataLayer: ${names.join(' → ')}\n      hits:      ${sent.join(' → ') || '(none)'}`,
      })
    }
    for (const op of align(names, sent)) {
      if (op.kind !== 'same') continue
      const { i, e, consent: c, uid: expectedUid, page: pv } = events[op.e]
      const h = hits[op.a]
      const where = `load ${l + 1} hit ${op.a + 1} ${h.en} (dataLayer #${i})`
      const bad = (rule: string, message: string) => out.push({ rule, where, message })
      const want = expectedHit(e)

      if (h.tid !== ctx.measurementId) bad('ga4-tid', `tid ${String(h.tid)}, expected ${ctx.measurementId}`)
      const params = [
        ...paramDiff(want.ep, h.ep, 'ep'),
        ...paramDiff(want.epn, h.epn, 'epn'),
        ...paramDiff(want.items, h.items, 'items'),
        ...paramDiff(want.currency, h.currency, 'cu'),
        ...paramDiff(pv.page_location, h.dl, 'dl'),
        ...paramDiff(pv.page_title, h.dt, 'dt'),
        ...(e.event === 'page_view' ? paramDiff(want.dr ?? '', h.dr ?? '', 'dr') : []),
      ]
      if (params.length === 1) bad('ga4-params', params[0])
      else if (params.length) bad('ga4-params', `${params.length} differences\n        ${params.join('\n        ')}`)
      // After gtag('set', { user_id: null }) gtag.js sends an empty uid= rather than none;
      // that counts as no user id. Before any sign-in the parameter must be absent.
      const uidOk = typeof expectedUid === 'string' ? h.uid === expectedUid : expectedUid === null ? !h.uid : h.uid === undefined
      if (!uidOk) {
        bad(
          'ga4-user-id',
          typeof expectedUid === 'string'
            ? `uid ${JSON.stringify(h.uid)}, expected the signed-in id ${expectedUid}`
            : `uid ${JSON.stringify(h.uid)} while no one is signed in${expectedUid === null ? ' (after a sign-out)' : ''}`,
        )
      }
      const gcs = gcsFor(c)
      if (h.gcs !== gcs) bad('ga4-consent', `gcs ${String(h.gcs)}, expected ${gcs} (ad_storage ${c.ad}, analytics_storage ${c.analytics} at this push)`)
    }
  })
  return out
}
