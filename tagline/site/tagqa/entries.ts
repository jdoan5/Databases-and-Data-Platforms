/**
 * What a journey records, and the normalised form the golden snapshots keep.
 *
 * A push is the JSON-safe copy taken the moment it was pushed (gtag() Arguments become
 * arrays, as the schema describes them). A recording is the pushes of each page load
 * in order. Pure: no browser, no file system, so the rules' self-test can use it too.
 */

export type Push = unknown

export interface Load {
  pushes: Push[]
}

export interface Recording {
  loads: Load[]
}

type Rec = Record<string, unknown>

export const isRecord = (x: unknown): x is Rec => typeof x === 'object' && x !== null && !Array.isArray(x)

export const eventName = (p: Push): string | undefined => (isRecord(p) && typeof p.event === 'string' ? p.event : undefined)

/** The 10 events that carry an `ecommerce` object (and so need a clear in front of them). */
export const ECOMMERCE_EVENTS = new Set([
  'view_item_list',
  'select_item',
  'view_item',
  'add_to_cart',
  'remove_from_cart',
  'view_cart',
  'begin_checkout',
  'add_shipping_info',
  'add_payment_info',
  'purchase',
])

export const isEcommerceClear = (p: Push): boolean =>
  isRecord(p) && Object.keys(p).length === 1 && 'ecommerce' in p && p.ecommerce === null

export const isConsentDefault = (p: Push): boolean => Array.isArray(p) && p[0] === 'consent' && p[1] === 'default'
export const isConsentUpdate = (p: Push): boolean => Array.isArray(p) && p[0] === 'consent' && p[1] === 'update'

/** gtag commands that exist only with GA4 forwarding on: js, config, set, event. */
export const isForwardingCommand = (p: Push): boolean =>
  Array.isArray(p) && (p[0] === 'js' || p[0] === 'config' || p[0] === 'set' || p[0] === 'event')

/** Google's own bookkeeping events (gtm.dom, gtm.load …), pushed by gtag.js once it loads. */
export const isGoogleInternal = (p: Push): boolean => eventName(p)?.startsWith('gtm.') ?? false

/** A short, stable description of a push, for messages and for aligning a diff. */
export function describe(p: Push): string {
  if (Array.isArray(p)) {
    const [command, action] = p
    if (command === 'consent') {
      const params = isRecord(p[2]) ? Object.values(p[2]) : []
      const state = params.length && params.every((v) => v === params[0]) ? String(params[0]) : 'mixed'
      return action === 'default' ? 'consent default' : `consent ${String(action)} ${state}`
    }
    if (command === 'event') return `gtag event ${String(action)}`
    return `gtag ${String(command)}`
  }
  const name = eventName(p)
  if (name) return name
  if (isEcommerceClear(p)) return '{ ecommerce: null }'
  if (isRecord(p) && 'user_id' in p) return p.user_id === null ? 'user_id null' : 'user_id'
  return isRecord(p) ? `{ ${Object.keys(p).join(', ')} }` : typeof p
}

/**
 * The label a push has in a journey's `expect` list, or null for pushes the list leaves
 * out: the ecommerce clears (their own rule), and what only appears with GA4 forwarding
 * on (gtag js/config/set/event, gtm.* events).
 */
export function sequenceLabel(p: Push): string | null {
  if (isEcommerceClear(p) || isForwardingCommand(p) || isGoogleInternal(p)) return null
  return describe(p)
}

export const sequenceOf = (load: Load): string[] =>
  load.pushes.map(sequenceLabel).filter((l): l is string => l !== null)

/** Drops `gtm.*` keys, which Google's libraries write into pushed objects once they load. */
function withoutGtmKeys(p: Push): Push {
  if (!isRecord(p)) return p
  return Object.fromEntries(Object.entries(p).filter(([k]) => !k.startsWith('gtm.')))
}

/**
 * The site's own pushes: what the dataLayer holds with GA4 forwarding off. With it on,
 * this is the same list, which is how the GA4 layer proves forwarding changes nothing
 * the dataLayer contract describes.
 */
export function sitePushes(rec: Recording): Recording {
  return {
    loads: rec.loads.map((l) => ({
      pushes: l.pushes.filter((p) => !isForwardingCommand(p) && !isGoogleInternal(p)).map(withoutGtmKeys),
    })),
  }
}

// ---- normalising for the golden snapshots -----------------------------------------

export interface Golden {
  journey: string
  title: string
  /** How the file was made and what the placeholders stand for. */
  note: string
  loads: { pushes: Push[] }[]
}

export const GOLDEN_NOTE =
  'Written by `npm run tagqa:update` (tagline/site/tagqa). The dataLayer of each page load, with GA4 forwarding off. ' +
  'Placeholders: <origin> is the dev server, <transaction_id:n> and <user_id:n> the n-th distinct id in the journey.'

const ORIGIN = /\bhttps?:\/\/(?:localhost|127\.0\.0\.1|\[::1\]):\d+/g
const ORDER_PATH = /\/order\/([A-Za-z0-9-]+)/g
const OPAQUE_ID = /^[0-9a-f]{32}$/

/**
 * Replaces what changes from run to run: the server's origin, transaction ids (in the
 * purchase and in /order/ URLs) and user ids, numbered by first appearance so a golden
 * still shows whether a later id is the same one again or a new one.
 */
export function normalise(rec: Recording): { pushes: Push[] }[] {
  const transactionIds = new Map<string, string>()
  const userIds = new Map<string, string>()
  const txn = (id: string) => {
    if (!transactionIds.has(id)) transactionIds.set(id, `<transaction_id:${transactionIds.size + 1}>`)
    return transactionIds.get(id)!
  }
  const uid = (id: string) => {
    if (!userIds.has(id)) userIds.set(id, `<user_id:${userIds.size + 1}>`)
    return userIds.get(id)!
  }

  // First pass: collect the ids in order of appearance.
  const visit = (x: unknown, key?: string): void => {
    if (typeof x === 'string') {
      if (key === 'transaction_id') txn(x)
      else if (key === 'user_id' && OPAQUE_ID.test(x)) uid(x)
      for (const m of x.matchAll(ORDER_PATH)) txn(m[1])
    } else if (Array.isArray(x)) x.forEach((v) => visit(v))
    else if (isRecord(x)) Object.entries(x).forEach(([k, v]) => visit(v, k))
  }
  rec.loads.forEach((l) => l.pushes.forEach((p) => visit(p)))

  const replace = (x: unknown, key?: string): unknown => {
    if (typeof x === 'string') {
      if (key === 'user_id' && userIds.has(x)) return userIds.get(x)
      let s = x.replace(ORIGIN, '<origin>')
      for (const [id, placeholder] of transactionIds) s = s.split(id).join(placeholder)
      return s
    }
    // gtag('js', new Date()): the date, only there with GA4 forwarding on.
    if (Array.isArray(x)) return x.map((v, i) => (x[0] === 'js' && i === 1 ? '<timestamp>' : replace(v)))
    if (isRecord(x)) return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, replace(v, k)]))
    return x
  }
  return rec.loads.map((l) => ({ pushes: l.pushes.map((p) => replace(p)) }))
}
