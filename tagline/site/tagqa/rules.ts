/**
 * The generic rules every journey's pushes must pass, as pure functions of a recording.
 *
 * Each rule returns violations with where (page load, dataLayer position, push) and a
 * sentence saying what is wrong. Nothing here touches a browser, so rules.spec.ts feeds
 * each rule a known-bad recording to prove it fires.
 *
 *   contract          every push valid against tagging/events.schema.json
 *   sequence          the journey's exact push sequence, per page load (plan.ts)
 *   consent-first     consent default is the first push of every page load, and only once
 *   ecommerce-clear   { ecommerce: null } immediately before every ecommerce event, and nowhere else
 *   page-view-once    no second page_view without a page change; page_referrer is the previous page
 *   purchase-once     at most one purchase per transaction_id across every page load of the journey
 *   value-math        value = Σ price × quantity in cents, at most 2 decimals, tax and shipping per the store
 *   list-consistency  select_item matches the list it came from; view_item the item selected; purchase the checkout
 *   no-pii            nothing email-shaped (@ or %40, decoded too) and none of the typed text, in any push
 *   user-id-opaque    no user_id is a hash of the typed text; as many distinct user ids as the plan says
 *
 * And, for the dataLayer layer only (the build has no measurement id):
 *   forwarding-off    no gtag js / config / set / event command and no gtm.* event
 */
import { createHash } from 'node:crypto'
import { findPii } from '../../simulator/src/hits.js'
import type { Contract } from '../src/tagging/contract.ts'
import {
  ECOMMERCE_EVENTS,
  describe,
  eventName,
  isConsentDefault,
  isEcommerceClear,
  isForwardingCommand,
  isGoogleInternal,
  isRecord,
  sequenceOf,
  type Push,
  type Recording,
} from './entries'
import { paramDiff } from './diff'
import { STORE, type ShippingTier } from './plan'

export interface Violation {
  /** One of the names above; the GA4 layer and the specs add their own (ga4-*, golden, page-error …). */
  rule: string
  /** "load 1 #7 add_to_cart", or "load 1" / "journey" for whole-list findings. */
  where: string
  message: string
}

type Rec = Record<string, unknown>
const at = (load: number, i: number, p: Push) => `load ${load + 1} #${i} ${describe(p)}`
const ecommerceOf = (p: Push): Rec | undefined => (isRecord(p) && isRecord(p.ecommerce) ? p.ecommerce : undefined)
const itemsOf = (p: Push): Rec[] => {
  const items = ecommerceOf(p)?.items
  return Array.isArray(items) ? items.filter(isRecord) : []
}
/** A value in a message, typed: "27.98" (a string) and 27.98 (a number) read differently. */
const show = (v: unknown): string => (v === undefined ? 'undefined' : JSON.stringify(v))
const cents = (usd: number) => Math.round(usd * 100)
/** True for the double nearest a 2-decimal amount (27.98), false for float noise (99.94999999999999). */
const hasAtMost2Decimals = (x: number) => Math.round(x * 100) / 100 === x

// ---- rules --------------------------------------------------------------------------

export function contractRule(rec: Recording, contract: Contract): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      const check = contract.check(p)
      if (!check.valid) out.push({ rule: 'contract', where: at(l, i, p), message: `does not match ${check.rule}: ${check.errors.join('; ')}` })
    }),
  )
  return out
}

export function sequenceRule(rec: Recording, expected: string[][]): Violation[] {
  const out: Violation[] = []
  if (rec.loads.length !== expected.length) {
    out.push({ rule: 'sequence', where: 'journey', message: `expected ${expected.length} page load(s), got ${rec.loads.length}` })
  }
  rec.loads.forEach((load, l) => {
    const want = expected[l] ?? []
    const got = sequenceOf(load)
    if (JSON.stringify(want) === JSON.stringify(got)) return
    let k = 0
    while (k < want.length && want[k] === got[k]) k++
    out.push({
      rule: 'sequence',
      where: `load ${l + 1}`,
      message:
        `step ${k + 1} of the sequence: expected ${want[k] === undefined ? 'nothing more' : `"${want[k]}"`}, ` +
        `got ${got[k] === undefined ? 'nothing more' : `"${got[k]}"`}\n` +
        `      expected: ${want.join(' → ')}\n` +
        `      actual:   ${got.join(' → ')}`,
    })
  })
  return out
}

export function consentFirstRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) => {
    const first = load.pushes[0]
    if (!isConsentDefault(first)) {
      const where = load.pushes.findIndex(isConsentDefault)
      out.push({
        rule: 'consent-first',
        where: `load ${l + 1}`,
        message:
          first === undefined
            ? 'nothing was pushed'
            : `the first push is ${describe(first)}, not consent default` + (where >= 0 ? ` (consent default is #${where})` : ' (no consent default at all)'),
      })
    }
    load.pushes.forEach((p, i) => {
      if (i > 0 && isConsentDefault(p)) out.push({ rule: 'consent-first', where: at(l, i, p), message: 'a second consent default in the same page load' })
    })
  })
  return out
}

export function ecommerceClearRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      const name = eventName(p)
      if (name && ECOMMERCE_EVENTS.has(name) && !isEcommerceClear(load.pushes[i - 1])) {
        const before = i > 0 ? describe(load.pushes[i - 1]) : 'nothing'
        out.push({ rule: 'ecommerce-clear', where: at(l, i, p), message: `no { ecommerce: null } immediately before it (the push before is ${before})` })
      }
      if (isEcommerceClear(p)) {
        const next = eventName(load.pushes[i + 1])
        if (!next || !ECOMMERCE_EVENTS.has(next)) {
          out.push({
            rule: 'ecommerce-clear',
            where: at(l, i, p),
            message: `not followed by an ecommerce event (the next push is ${i + 1 < load.pushes.length ? describe(load.pushes[i + 1]) : 'nothing'})`,
          })
        }
      }
    }),
  )
  return out
}

export function pageViewOnceRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) => {
    let previous: { i: number; location: unknown } | null = null
    load.pushes.forEach((p, i) => {
      if (eventName(p) !== 'page_view' || !isRecord(p)) return
      if (previous) {
        if (p.page_location === previous.location) {
          out.push({
            rule: 'page-view-once',
            where: at(l, i, p),
            message: `a second page_view for ${String(p.page_location)} with no page change in between (the first is #${previous.i})`,
          })
        } else if (p.page_referrer !== previous.location) {
          out.push({
            rule: 'page-view-once',
            where: at(l, i, p),
            message: `page_referrer is ${JSON.stringify(p.page_referrer)}, expected the previous page_view's page_location ${JSON.stringify(previous.location)}`,
          })
        }
      }
      previous = { i, location: p.page_location }
    })
  })
  return out
}

export function purchaseOnceRule(rec: Recording): Violation[] {
  const seen = new Map<string, string[]>()
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      if (eventName(p) !== 'purchase') return
      const id = String(ecommerceOf(p)?.transaction_id)
      seen.set(id, [...(seen.get(id) ?? []), `load ${l + 1} #${i}`])
    }),
  )
  return [...seen]
    .filter(([, where]) => where.length > 1)
    .map(([id, where]) => ({
      rule: 'purchase-once',
      where: 'journey',
      message: `purchase pushed ${where.length} times for transaction_id ${id} (${where.join(', ')})`,
    }))
}

export function valueMathRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) => {
    let tier: ShippingTier | null = null
    load.pushes.forEach((p, i) => {
      const name = eventName(p)
      const e = ecommerceOf(p)
      if (!name || !e) return
      const where = at(l, i, p)
      const bad = (message: string) => out.push({ rule: 'value-math', where, message })
      const items = itemsOf(p)

      for (const [k, v] of Object.entries({ value: e.value, tax: e.tax, shipping: e.shipping }))
        if (typeof v === 'number' && !hasAtMost2Decimals(v)) bad(`${k} ${v} has more than 2 decimals`)
      items.forEach((it, n) => {
        if (typeof it.price === 'number' && !hasAtMost2Decimals(it.price)) bad(`items[${n}].price ${it.price} has more than 2 decimals`)
      })

      if (typeof e.value === 'number') {
        const sum = items.reduce((s, it) => s + cents(Number(it.price)) * Number(it.quantity), 0)
        if (cents(e.value) !== sum || !Number.isFinite(sum)) bad(`value is ${e.value}, but Σ price × quantity over items is ${sum / 100}`)
      }
      if (name === 'view_item_list' || name === 'select_item' || name === 'view_item') {
        items.forEach((it, n) => {
          if (it.quantity !== 1) bad(`items[${n}].quantity is ${String(it.quantity)}; a list or product view shows 1`)
        })
      }
      if (name === 'view_item_list') {
        items.forEach((it, n) => {
          if (it.index !== n) bad(`items[${n}].index is ${String(it.index)}, expected ${n} (display order)`)
        })
      }
      if (name === 'add_shipping_info' && typeof e.shipping_tier === 'string') tier = e.shipping_tier as ShippingTier
      if (name === 'purchase' && typeof e.value === 'number') {
        const tax = Math.round(cents(e.value) * STORE.taxRate) / 100
        if (e.tax !== tax) bad(`tax is ${String(e.tax)}, expected ${tax} (${STORE.taxRate * 100}% of value ${e.value})`)
        if (tier && e.shipping !== STORE.shipping[tier]) bad(`shipping is ${String(e.shipping)}, expected ${STORE.shipping[tier]} for the ${tier} tier chosen in add_shipping_info`)
      }
    })
  })
  return out
}

export function listConsistencyRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) => {
    let lastList: { i: number; e: Rec; items: Rec[] } | null = null
    let selected: { i: number; itemId: unknown } | null = null
    let checkout: { i: number; p: Push } | null = null
    load.pushes.forEach((p, i) => {
      const name = eventName(p)
      const e = ecommerceOf(p)
      if (!name || !e) return
      const where = at(l, i, p)
      const bad = (message: string) => out.push({ rule: 'list-consistency', where, message })
      if (name === 'view_item_list') lastList = { i, e, items: itemsOf(p) }
      if (name === 'select_item') {
        const item = itemsOf(p)[0]
        if (!lastList) bad('no view_item_list before it in this page load')
        else {
          const list: { i: number; e: Rec; items: Rec[] } = lastList
          if (e.item_list_id !== list.e.item_list_id || e.item_list_name !== list.e.item_list_name)
            bad(`list ${show(e.item_list_id)} / ${show(e.item_list_name)}, but the view_item_list it came from (#${list.i}) is ${show(list.e.item_list_id)} / ${show(list.e.item_list_name)}`)
          const shown = typeof item?.index === 'number' ? list.items[item.index] : undefined
          if (!shown) bad(`items[0].index ${show(item?.index)} is not a position in the view_item_list (#${list.i})`)
          else {
            const diff = paramDiff(shown, item)
            if (diff.length) bad(`the item differs from position ${show(item.index)} of the view_item_list (#${list.i}): ${diff.join('; ')}`)
          }
        }
        selected = { i, itemId: item?.item_id }
      }
      if (name === 'view_item' && selected) {
        const itemId = itemsOf(p)[0]?.item_id
        if (itemId !== selected.itemId) bad(`view_item for ${show(itemId)}, but select_item (#${selected.i}) picked ${show(selected.itemId)}`)
        selected = null
      }
      if (name === 'begin_checkout') checkout = { i, p }
      if (name === 'purchase' && checkout) {
        const c: { i: number; p: Push } = checkout
        const ce = ecommerceOf(c.p)!
        if (ce.value !== e.value) bad(`value ${show(e.value)}, but begin_checkout (#${c.i}) had ${show(ce.value)}`)
        const diff = paramDiff(itemsOf(c.p), itemsOf(p), 'items')
        if (diff.length) bad(`items differ from begin_checkout (#${c.i}): ${diff.join('; ')}`)
      }
    })
  })
  return out
}

/** Up to three rounds of URL-decoding, as the simulator's findPii does. */
function decodeAll(s: string): string {
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
 * What is wrong with one value, if anything: email-shaped (raw or URL-encoded up to three
 * times; the simulator's findPii, the check it runs on every hit), the journey's typed
 * text, or the typed text's part before the @, in any case.
 */
export function piiKinds(value: string, typed: readonly string[] = []): string[] {
  const kinds: string[] = findPii(value).length ? ['email-shaped'] : []
  const forms = [value, decodeAll(value)].map((t) => t.toLowerCase())
  for (const t of typed) {
    const whole = t.toLowerCase()
    const local = whole.split('@')[0]
    if (forms.some((f) => f.includes(whole))) kinds.push('the text typed into the site')
    else if (local.length >= 4 && forms.some((f) => f.includes(local))) kinds.push('the part before the @ of the text typed into the site')
  }
  return kinds
}

/** Every string in a push, keys included, with its path ("ecommerce.items[0].item_name"). */
function strings(x: unknown, path = ''): [string, string][] {
  if (typeof x === 'string') return [[path || '(value)', x]]
  if (Array.isArray(x)) return x.flatMap((v, i) => strings(v, `${path}[${i}]`))
  if (isRecord(x))
    return Object.entries(x).flatMap(([k, v]): [string, string][] => [[`${path ? `${path}.` : ''}${k} (the key)`, k], ...strings(v, path ? `${path}.${k}` : k)])
  return []
}

export function noPiiRule(rec: Recording, typed: readonly string[] = []): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      for (const [path, value] of strings(p)) {
        const kinds = piiKinds(value, typed)
        if (kinds.length) out.push({ rule: 'no-pii', where: at(l, i, p), message: `${path} ${JSON.stringify(value.slice(0, 120))}: ${kinds.join('; ')}` })
      }
    }),
  )
  return out
}

const DIGESTS = ['md5', 'sha1', 'sha256', 'sha512'] as const

/** The hex digests a user_id derived from the typed text would be made of: as typed, and trimmed and lowercased. */
function digestsOf(typed: readonly string[]): { hex: string; label: string }[] {
  return typed.flatMap((t) =>
    [
      ['as typed', t],
      ['trimmed and lowercased', t.trim().toLowerCase()],
    ].flatMap(([form, text]) => DIGESTS.map((h) => ({ hex: createHash(h).update(text).digest('hex'), label: `the ${h.toUpperCase()} of the typed text (${form})` }))),
  )
}

/**
 * The site's user_id must be opaque (tagging plan: 128 random bits, not derived from the
 * email). Two checks: no user_id is, or is cut from, a common hash of the typed text; and,
 * when the plan gives `userIds`, the journey pushes exactly that many distinct ids (the
 * account journey signs the same email up again after clearing the browser's storage,
 * which a random id tells apart and any id computed from the email does not).
 */
export function userIdOpaqueRule(rec: Recording, typed: readonly string[] = [], userIds?: number): Violation[] {
  const out: Violation[] = []
  const digests = digestsOf(typed)
  const ids = new Set<string>()
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      if (!isRecord(p) || eventName(p) !== undefined || typeof p.user_id !== 'string') return
      const id = p.user_id.toLowerCase()
      ids.add(id)
      const d = id.length >= 16 ? digests.find((x) => x.hex.includes(id)) : undefined
      if (d) {
        out.push({
          rule: 'user-id-opaque',
          where: at(l, i, p),
          message: `user_id ${show(p.user_id)} is ${d.hex === id ? '' : 'cut from '}${d.label}: derived from the email, so not opaque`,
        })
      }
    }),
  )
  if (userIds !== undefined && ids.size !== userIds) {
    out.push({
      rule: 'user-id-opaque',
      where: 'journey',
      message: `${ids.size} distinct user_id(s) pushed, the plan expects ${userIds}: the same email signed up again with the browser's storage cleared must get a new id (one that comes back is computed from the email)`,
    })
  }
  return out
}

/**
 * With no measurement id the site forwards nothing to GA4, so a gtag js / config / set /
 * event command, or a gtm.* event from gtag.js, is a push the build should not make. Not
 * part of checkJourney: the GA4 layer's build has forwarding on.
 */
export function forwardingOffRule(rec: Recording): Violation[] {
  const out: Violation[] = []
  rec.loads.forEach((load, l) =>
    load.pushes.forEach((p, i) => {
      if (isForwardingCommand(p) || isGoogleInternal(p)) {
        out.push({ rule: 'forwarding-off', where: at(l, i, p), message: 'pushed with GA4 forwarding off (this build has no measurement id)' })
      }
    }),
  )
  return out
}

// ---- all of them ----------------------------------------------------------------------

export interface RuleContext {
  contract: Contract
  expect: string[][]
  typed?: readonly string[]
  /** plan.ts: how many distinct user ids the journey must push. */
  userIds?: number
}

/**
 * Every rule, on every push the page made, so each finding names the push's real
 * dataLayer position. With GA4 forwarding on, the forwarding commands (gtag js, config,
 * set, event) and gtag.js's gtm.* events are in the list too: the contract rule checks
 * them, the sequence leaves them out, and the other rules only look at the site's own
 * pushes, which gtag.js cannot slip between (a clear and its event are pushed in one go).
 */
export function checkJourney(rec: Recording, ctx: RuleContext): Violation[] {
  return [
    ...contractRule(rec, ctx.contract),
    ...sequenceRule(rec, ctx.expect),
    ...consentFirstRule(rec),
    ...ecommerceClearRule(rec),
    ...pageViewOnceRule(rec),
    ...purchaseOnceRule(rec),
    ...valueMathRule(rec),
    ...listConsistencyRule(rec),
    ...noPiiRule(rec, ctx.typed),
    ...userIdOpaqueRule(rec, ctx.typed, ctx.userIds),
  ]
}

export function formatViolations(violations: readonly Violation[]): string {
  const width = Math.max(...violations.map((v) => v.rule.length))
  return violations.map((v) => `  ✗ ${v.rule.padEnd(width)}  ${v.where}: ${v.message}`).join('\n')
}
