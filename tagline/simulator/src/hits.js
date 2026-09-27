/**
 * Reading what gtag.js sends to GA4.
 *
 * A GA4 web hit is a request to <host>/g/collect. Parameters shared by every event in
 * the request (tid, cid, sid, consent state, often dl/dt) are in the query string.
 * gtag.js batches: when a request carries several events, each one is a line of the
 * POST body in the same key=value&... form, and a line's keys override the query's.
 * A request without a body is one event described entirely by its query string.
 *
 * The parameter names are not a published API; these are the ones gtag.js is widely
 * documented to use and the ones this project needs:
 *   en event name · ep.<k> / epn.<k> string / number event params · up.<k> / upn.<k> user
 *   properties · pr1..prN items (~-separated 2-letter keys) · cu currency · uid user_id
 *   cid client id (becomes user_pseudo_id) · sid / sct session id / count · seg session
 *   engaged · _ss session start · _fv first visit · _nsi new session id · _et engagement
 *   ms · dl / dr / dt page location / referrer / title · gcs consent state (G1<ad><analytics>,
 *   G111 = both granted, G100 = both denied) · _s hit sequence in the page · _p page load id
 */

/** Hosts gtag.js sends measurement to. Everything Google-owned is intercepted, not only these. */
export const GOOGLE_HOST = /(^|\.)(google-analytics\.com|analytics\.google\.com|googletagmanager\.com|doubleclick\.net|google\.com)$/i

export const isCollect = (url) => GOOGLE_HOST.test(url.hostname) && /\/g\/collect$/.test(url.pathname)
export const isGtagScript = (url) => /(^|\.)googletagmanager\.com$/i.test(url.hostname) && url.pathname === '/gtag/js'

const ITEM_KEYS = {
  id: 'item_id',
  nm: 'item_name',
  br: 'item_brand',
  ca: 'item_category',
  c2: 'item_category2',
  c3: 'item_category3',
  c4: 'item_category4',
  c5: 'item_category5',
  va: 'item_variant',
  pr: 'price',
  qt: 'quantity',
  lp: 'index',
  li: 'item_list_id',
  ln: 'item_list_name',
  cp: 'coupon',
  ds: 'discount',
  af: 'affiliation',
}
const NUMERIC_ITEM_KEYS = new Set(['price', 'quantity', 'index', 'discount'])

/** "idTL-DRK-001~nmCeramic Mug~pr13.99~qt2" → { item_id, item_name, price, quantity } */
export function parseItem(encoded) {
  const item = {}
  for (const part of encoded.split('~')) {
    if (part.length < 2) continue
    const key = ITEM_KEYS[part.slice(0, 2)] ?? part.slice(0, 2)
    const value = part.slice(2)
    item[key] = NUMERIC_ITEM_KEYS.has(key) && value !== '' && !Number.isNaN(Number(value)) ? Number(value) : value
  }
  return item
}

/** utm_* from a page URL, or null when there are none. */
export function campaignFromUrl(dl) {
  if (!dl) return null
  let url
  try {
    url = new URL(dl)
  } catch {
    return null
  }
  const out = {}
  for (const key of ['source', 'medium', 'campaign', 'content', 'term']) {
    const v = url.searchParams.get(`utm_${key}`)
    if (v !== null) out[key] = v
  }
  return Object.keys(out).length ? out : null
}

const num = (v) => (v === undefined || v === '' || Number.isNaN(Number(v)) ? undefined : Number(v))

/** One event's params (query merged with its body line) → a readable record. */
export function normaliseHit(params) {
  const ep = {}
  const epn = {}
  const up = {}
  const items = []
  for (const [k, v] of Object.entries(params)) {
    if (k.startsWith('ep.')) ep[k.slice(3)] = v
    else if (k.startsWith('epn.')) epn[k.slice(4)] = Number(v)
    else if (k.startsWith('up.') || k.startsWith('upn.')) up[k.slice(k.indexOf('.') + 1)] = v
    else if (/^pr\d+$/.test(k)) items[Number(k.slice(2)) - 1] = parseItem(v)
  }
  return {
    en: params.en,
    tid: params.tid,
    cid: params.cid,
    sid: params.sid,
    sct: num(params.sct),
    seg: num(params.seg),
    uid: params.uid,
    dl: params.dl,
    dr: params.dr,
    dt: params.dt,
    gcs: params.gcs,
    session_start: params._ss !== undefined,
    first_visit: params._fv !== undefined,
    new_session_id: params._nsi !== undefined,
    engagement_ms: num(params._et),
    seq: num(params._s),
    page_load_id: params._p,
    currency: params.cu,
    campaign: campaignFromUrl(params.dl),
    ep,
    epn,
    up,
    items: items.filter(Boolean),
    params,
  }
}

/** Every event in one /g/collect request. */
export function parseCollect(requestUrl, body) {
  const url = new URL(requestUrl)
  const shared = Object.fromEntries(url.searchParams)
  const lines = body ? String(body).split(/\r?\n/).filter((l) => l.trim() !== '') : []
  if (lines.length === 0) return [normaliseHit(shared)]
  return lines.map((line) => normaliseHit({ ...shared, ...Object.fromEntries(new URLSearchParams(line)) }))
}

/** gcs → what the hit says about analytics_storage. */
export function analyticsConsent(gcs) {
  if (!gcs || !/^G1[01-][01-]$/.test(gcs)) return 'unknown'
  return gcs[3] === '1' ? 'granted' : gcs[3] === '0' ? 'denied' : 'unknown'
}

// ---------------------------------------------------------------------------------
// Personal data

/** Same pattern as the site's LOOKS_LIKE_EMAIL (site/src/tagging/pii.ts): raw or URL-encoded @. */
export const LOOKS_LIKE_EMAIL = /[^\s@&=?]+(@|%40)[^\s@&=?]+\.[^\s@&=?]+/i

const decodeAll = (s) => {
  let out = s
  for (let i = 0; i < 3; i++) {
    try {
      const next = decodeURIComponent(out.replace(/\+/g, ' '))
      if (next === out) break
      out = next
    } catch {
      break
    }
  }
  return out
}

/**
 * Where a request carries personal data: anything email-shaped, raw or encoded (up to
 * three rounds of URL-encoding), and any of the run's own synthetic emails or their
 * local parts, in any case. `raw` is the full URL plus body exactly as sent.
 */
export function findPii(raw, knownEmails = []) {
  const findings = []
  const forms = [raw, decodeAll(raw)]
  for (const text of forms) {
    const m = text.match(LOOKS_LIKE_EMAIL)
    if (m) findings.push(`email-shaped text: ${m[0].slice(0, 80)}`)
  }
  const lower = forms.map((t) => t.toLowerCase())
  for (const email of knownEmails) {
    const e = email.toLowerCase()
    const local = e.split('@')[0]
    if (lower.some((t) => t.includes(e))) findings.push(`a simulated person's email: ${e}`)
    else if (lower.some((t) => t.includes(local))) findings.push(`a simulated person's email local part: ${local}`)
  }
  return [...new Set(findings)]
}
