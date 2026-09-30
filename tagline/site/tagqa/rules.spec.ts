/**
 * The checks, checked: no browser. A small, correct recording passes every rule; the
 * same recording with one fault each (the regressions the suite exists to catch) fails
 * exactly the rule meant for it. Also the golden diff, the normaliser, the GA4 hit
 * checks, and the plan itself (it covers every event in the contract).
 */
import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { diffJourney } from './diff'
import { normalise, sequenceOf, type Push, type Recording } from './entries'
import { checkHits, expectedHit, withRetries, type Hit } from './ga4'
import { CONTRACT_EVENTS, contract } from './harness'
import { JOURNEYS } from './plan'
import { checkJourney, forwardingOffRule, type Violation } from './rules'

const ORIGIN = 'http://localhost:5182'
const DENIED = { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }
const GRANTED = { analytics_storage: 'granted', ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' }
const tee = { item_id: 'TL-APP-001', item_name: 'Classic Logo Tee', item_brand: 'Tagline Supply', item_category: 'Apparel', item_variant: 'Navy', price: 24 }
const mug = { item_id: 'TL-DRK-001', item_name: 'Ceramic Mug', item_brand: 'Tagline Supply', item_category: 'Drinkware', item_variant: 'White', price: 13.99 }
const TXN = 'TL-MG3K2ZQ1-0A7F3KD'
const USER = '3f1c9a0b7d2e4f6a8b0c1d2e3f4a5b6c'

/** A correct journey: list → product → add 2 → sign up → checkout → purchase, then a reload. */
function good(): Recording {
  const cart = [{ ...mug, quantity: 2 }]
  return {
    loads: [
      {
        pushes: [
          ['consent', 'default', DENIED],
          { event: 'page_view', page_location: `${ORIGIN}/`, page_title: 'All products · Tagline Supply' },
          { ecommerce: null },
          {
            event: 'view_item_list',
            ecommerce: { currency: 'USD', item_list_id: 'all_products', item_list_name: 'All products', items: [{ ...tee, quantity: 1, index: 0 }, { ...mug, quantity: 1, index: 1 }] },
          },
          ['consent', 'update', GRANTED],
          { ecommerce: null },
          { event: 'select_item', ecommerce: { currency: 'USD', item_list_id: 'all_products', item_list_name: 'All products', items: [{ ...mug, quantity: 1, index: 1 }] } },
          { event: 'page_view', page_location: `${ORIGIN}/product/TL-DRK-001`, page_title: 'Ceramic Mug, White · Tagline Supply', page_referrer: `${ORIGIN}/` },
          { ecommerce: null },
          { event: 'view_item', ecommerce: { currency: 'USD', value: 13.99, items: [{ ...mug, quantity: 1 }] } },
          { ecommerce: null },
          { event: 'add_to_cart', ecommerce: { currency: 'USD', value: 27.98, items: cart } },
          { event: 'search', search_term: '[email]' },
          { event: 'page_view', page_location: `${ORIGIN}/search?q=%5Bemail%5D`, page_title: 'Search: [email] · Tagline Supply', page_referrer: `${ORIGIN}/product/TL-DRK-001` },
          { user_id: USER },
          { event: 'sign_up', method: 'email' },
          { event: 'page_view', page_location: `${ORIGIN}/checkout`, page_title: 'Checkout · Tagline Supply', page_referrer: `${ORIGIN}/search?q=%5Bemail%5D` },
          { ecommerce: null },
          { event: 'begin_checkout', ecommerce: { currency: 'USD', value: 27.98, items: cart } },
          { ecommerce: null },
          { event: 'add_shipping_info', ecommerce: { currency: 'USD', value: 27.98, shipping_tier: 'Express', items: cart } },
          { event: 'page_view', page_location: `${ORIGIN}/order/${TXN}`, page_title: 'Order confirmed · Tagline Supply', page_referrer: `${ORIGIN}/checkout` },
          { ecommerce: null },
          { event: 'purchase', ecommerce: { transaction_id: TXN, currency: 'USD', value: 27.98, tax: 2.24, shipping: 12, items: cart } },
        ],
      },
      {
        pushes: [
          ['consent', 'default', DENIED],
          ['consent', 'update', GRANTED],
          { user_id: USER },
          { event: 'page_view', page_location: `${ORIGIN}/order/${TXN}`, page_title: 'Order confirmed · Tagline Supply' },
        ],
      },
    ],
  }
}

const EXPECT = [
  [
    'consent default',
    'page_view',
    'view_item_list',
    'consent update granted',
    'select_item',
    'page_view',
    'view_item',
    'add_to_cart',
    'search',
    'page_view',
    'user_id',
    'sign_up',
    'page_view',
    'begin_checkout',
    'add_shipping_info',
    'page_view',
    'purchase',
  ],
  ['consent default', 'consent update granted', 'user_id', 'page_view'],
]
const TYPED = ['qa.shopper@example.com']

const check = (rec: Recording) => checkJourney(rec, { contract, expect: EXPECT, typed: TYPED })
const rulesOf = (vs: Violation[]) => [...new Set(vs.map((v) => v.rule))].sort()
type Rec = Record<string, any>
/** The recording with one change made to a copy of it. */
function mutated(change: (loads: Push[][]) => void): Recording {
  const rec = good()
  const loads = rec.loads.map((l) => l.pushes)
  change(loads)
  return { loads: loads.map((pushes) => ({ pushes })) }
}
const find = (pushes: Push[], name: string) => pushes.findIndex((p) => (p as Rec).event === name)

test('a correct recording passes every rule', () => {
  expect(check(good())).toEqual([])
})

test('item_brand dropped from add_to_cart: contract', () => {
  const vs = check(mutated(([l]) => delete ((l[find(l, 'add_to_cart')] as Rec).ecommerce.items[0] as Rec).item_brand))
  expect(rulesOf(vs)).toEqual(['contract'])
  expect(vs[0].message).toContain('/ecommerce/items/0: missing "item_brand"')
})

test('page_view twice on one route change: page-view-once and sequence', () => {
  const vs = check(mutated(([l]) => l.splice(8, 0, { ...(l[7] as Rec), page_referrer: `${ORIGIN}/product/TL-DRK-001` })))
  expect(rulesOf(vs)).toEqual(['page-view-once', 'sequence'])
  expect(vs.find((v) => v.rule === 'page-view-once')?.message).toContain('a second page_view for http://localhost:5182/product/TL-DRK-001')
})

test('purchase pushed again after the reload: purchase-once and sequence', () => {
  const vs = check(
    mutated(([l1, l2]) => {
      const i = find(l1, 'purchase')
      l2.push(l1[i - 1], l1[i])
    }),
  )
  expect(rulesOf(vs)).toEqual(['purchase-once', 'sequence'])
  expect(vs.find((v) => v.rule === 'purchase-once')?.message).toBe(`purchase pushed 2 times for transaction_id ${TXN} (load 1 #23, load 2 #5)`)
})

test('the typed email in search_term: contract and no-pii', () => {
  const vs = check(mutated(([l]) => ((l[find(l, 'search')] as Rec).search_term = 'Qa.Shopper@Example.com')))
  expect(rulesOf(vs)).toEqual(['contract', 'no-pii'])
  expect(vs.find((v) => v.rule === 'no-pii')?.message).toContain('the text typed into the site')
})

test('an email in a URL, URL-encoded: contract and no-pii', () => {
  const vs = check(mutated(([l]) => ((l[13] as Rec).page_location = `${ORIGIN}/search?q=jo%40example.com`)))
  expect(rulesOf(vs)).toEqual(['contract', 'no-pii', 'page-view-once'])
})

test('a forgotten { ecommerce: null }: ecommerce-clear', () => {
  const vs = check(mutated(([l]) => l.splice(find(l, 'view_item') - 1, 1)))
  expect(rulesOf(vs)).toEqual(['ecommerce-clear'])
  expect(vs[0].message).toBe('no { ecommerce: null } immediately before it (the push before is page_view)')
})

test('a clear before a non-ecommerce event: ecommerce-clear', () => {
  const vs = check(mutated(([l]) => l.splice(find(l, 'search'), 0, { ecommerce: null })))
  expect(rulesOf(vs)).toEqual(['ecommerce-clear'])
})

test('consent default not first: consent-first and sequence', () => {
  const vs = check(mutated(([l]) => l.splice(0, 2, l[1], l[0])))
  expect(rulesOf(vs)).toEqual(['consent-first', 'sequence'])
  expect(vs.find((v) => v.rule === 'consent-first')?.message).toBe('the first push is page_view, not consent default (consent default is #1)')
})

test('value off by a cent, float noise, wrong tax and shipping: value-math', () => {
  expect(rulesOf(check(mutated(([l]) => ((l[find(l, 'add_to_cart')] as Rec).ecommerce.value = 27.97))))).toEqual(['value-math'])
  const noise = check(
    mutated(([l]) => {
      const e = (l[find(l, 'add_to_cart')] as Rec).ecommerce
      e.items = [{ ...tee, price: 19.99, quantity: 5 }]
      e.value = 99.94999999999999
    }),
  )
  expect(noise.map((v) => v.message)).toContain('value 99.94999999999999 has more than 2 decimals')
  const tax = check(mutated(([l]) => ((l[find(l, 'purchase')] as Rec).ecommerce.tax = 2.23)))
  expect(tax.map((v) => v.message)).toEqual(['tax is 2.23, expected 2.24 (8% of value 27.98)'])
  const ship = check(mutated(([l]) => ((l[find(l, 'purchase')] as Rec).ecommerce.shipping = 5)))
  expect(ship.map((v) => v.message)).toEqual(['shipping is 5, expected 12 for the Express tier chosen in add_shipping_info'])
})

test('select_item from another list, view_item for another product: list-consistency', () => {
  const vs = check(
    mutated(([l]) => {
      ;(l[find(l, 'select_item')] as Rec).ecommerce.item_list_id = 'search_results'
      ;(l[find(l, 'view_item')] as Rec).ecommerce.items[0] = { ...tee, quantity: 1 }
      ;(l[find(l, 'view_item')] as Rec).ecommerce.value = 24
    }),
  )
  expect(rulesOf(vs)).toEqual(['list-consistency'])
  expect(vs.map((v) => v.message)).toEqual([
    'list "search_results" / "All products", but the view_item_list it came from (#3) is "all_products" / "All products"',
    'view_item for "TL-APP-001", but select_item (#6) picked "TL-DRK-001"',
  ])
})

test('purchase value as a string: list-consistency shows the types apart', () => {
  const vs = check(mutated(([l]) => ((l[find(l, 'purchase')] as Rec).ecommerce.value = '27.98')))
  expect(vs.find((v) => v.rule === 'list-consistency')?.message).toBe('value "27.98", but begin_checkout (#18) had 27.98')
})

test('a user_id computed from the typed email: user-id-opaque', () => {
  const derived = createHash('sha256').update(TYPED[0].toLowerCase()).digest('hex').slice(0, 32)
  const vs = check(
    mutated((loads) => {
      for (const l of loads) for (const p of l) if ((p as Rec).user_id !== undefined && !(p as Rec).event) (p as Rec).user_id = derived
    }),
  )
  expect(rulesOf(vs)).toEqual(['user-id-opaque'])
  expect(vs[0].message).toBe(`user_id "${derived}" is cut from the SHA256 of the typed text (as typed): derived from the email, so not opaque`)
})

test('the same id where the plan expects a new one: user-id-opaque', () => {
  const vs = checkJourney(good(), { contract, expect: EXPECT, typed: TYPED, userIds: 2 })
  expect(rulesOf(vs)).toEqual(['user-id-opaque'])
  expect(vs[0].message).toMatch(/^1 distinct user_id\(s\) pushed, the plan expects 2: /)
  expect(checkJourney(good(), { contract, expect: EXPECT, typed: TYPED, userIds: 1 })).toEqual([])
})

test('a gtag command with GA4 forwarding off: forwarding-off', () => {
  expect(forwardingOffRule(good())).toEqual([])
  const vs = forwardingOffRule(mutated(([l]) => l.splice(2, 0, ['event', 'page_view', { page_location: `${ORIGIN}/` }], { event: 'gtm.dom' })))
  expect(vs.map((v) => `${v.rule} ${v.where}`)).toEqual(['forwarding-off load 1 #2 gtag event page_view', 'forwarding-off load 1 #3 gtm.dom'])
})

test('an event missing from the sequence: sequence names the first difference', () => {
  const vs = check(mutated(([l]) => l.splice(find(l, 'sign_up'), 1)))
  expect(rulesOf(vs)).toEqual(['sequence'])
  expect(vs[0].message).toMatch(/^step 12 of the sequence: expected "sign_up", got "page_view"/)
})

test('golden diff: one push too many, one parameter missing', () => {
  const golden = normalise(good())
  const actual = normalise(
    mutated(([l]) => {
      delete ((l[find(l, 'add_to_cart')] as Rec).ecommerce.items[0] as Rec).item_brand
      l.splice(8, 0, { ...(l[7] as Rec) })
    }),
  )
  const diff = diffJourney(golden, actual)
  expect(diff[0]).toBe('load 1 (golden 24 pushes, actual 25):')
  expect(diff).toContain('  + #8 page_view   unexpected (pushed, not in the golden)')
  expect(diff).toContain('  ~ #12 add_to_cart')
  expect(diff).toContain('        ecommerce.items[0].item_brand: expected "Tagline Supply", got (missing)')
  expect(diffJourney(golden, normalise(good()))).toEqual([])
})

test('golden diff: a page_view too many before another page, and a page_view whose URL changed', () => {
  const golden = normalise(good())
  const extra = diffJourney(golden, normalise(mutated(([l]) => l.splice(16, 0, { ...(l[13] as Rec), page_referrer: (l[13] as Rec).page_location }))))
  expect(extra.filter((line) => /^ {2}[-+~]/.test(line))).toEqual(['  + #16 page_view   unexpected (pushed, not in the golden)'])
  const moved = diffJourney(golden, normalise(mutated(([l]) => ((l[16] as Rec).page_location = `${ORIGIN}/cart`))))
  expect(moved.slice(1)).toEqual(['  ~ #16 page_view', '        page_location: expected "<origin>/checkout", got "<origin>/cart"'])
})

test('golden diff: a push in another place shows once, as moved; a missing push names its golden position', () => {
  const golden = normalise(good())
  // user_id pushed after sign_up instead of before it
  const moved = diffJourney(golden, normalise(mutated(([l]) => l.splice(14, 2, l[15], l[14]))))
  expect(moved.slice(1)).toEqual(['  ↕ #15 user_id   moved (golden #14)'])
  const gone = diffJourney(golden, normalise(mutated(([l]) => l.splice(14, 1))))
  expect(gone.slice(1)).toEqual(['  - golden #14 user_id   missing (in the golden, not pushed)', `        {"user_id":"<user_id:1>"}`])
})

test('the gtag.js download is tried again after a failure, three times at most', async () => {
  const waits: number[] = []
  const sleep = async (ms: number) => void waits.push(ms)
  let calls = 0
  const flaky = async () => {
    if (++calls < 3) throw new Error('read ETIMEDOUT')
    return 'gtag.js'
  }
  expect(await withRetries(flaky, 3, 1000, sleep)).toBe('gtag.js')
  expect(waits).toEqual([1000, 2000])
  calls = -10
  await expect(withRetries(flaky, 3, 1000, sleep)).rejects.toThrow('read ETIMEDOUT')
})

test('normalise: origin, transaction and user ids become placeholders; SKUs stay', () => {
  const [l1, l2] = normalise(good())
  const purchase = l1.pushes.find((p) => (p as Rec).event === 'purchase') as Rec
  expect(purchase.ecommerce.transaction_id).toBe('<transaction_id:1>')
  expect(purchase.ecommerce.items[0].item_id).toBe('TL-DRK-001')
  expect(l2.pushes[2]).toEqual({ user_id: '<user_id:1>' })
  expect(l2.pushes[3]).toEqual({ event: 'page_view', page_location: '<origin>/order/<transaction_id:1>', page_title: 'Order confirmed · Tagline Supply' })
})

// ---- the GA4 hit checks --------------------------------------------------------------

/** The hits gtag.js would send for good(): one per event, params translated, state tracked. */
function hitsFor(rec: Recording): Hit[] {
  const out: Hit[] = []
  rec.loads.forEach((load, l) => {
    let gcs = 'G100'
    let uid: string | undefined
    let page: Rec = {}
    for (const p of load.pushes as any[]) {
      if (Array.isArray(p) && p[0] === 'consent') gcs = p[2].analytics_storage === 'granted' ? 'G111' : 'G100'
      else if ('user_id' in p) uid = p.user_id ?? undefined
      else if (p.event) {
        if (p.event === 'page_view') page = p
        const want = expectedHit(p)
        out.push({
          en: p.event,
          tid: 'G-TAGQA0000',
          uid,
          gcs,
          dl: page.page_location,
          dt: page.page_title,
          dr: p.event === 'page_view' ? page.page_referrer : undefined,
          currency: want.currency as string | undefined,
          page_load_id: String(l),
          ep: want.ep as Record<string, string>,
          epn: want.epn as Record<string, number>,
          items: want.items as Record<string, unknown>[],
        })
      }
    }
  })
  return out
}
const capture = (hits: Hit[]) => ({ siteHits: () => hits, requests: [], leaks: [], scriptError: null })
const hitCheck = (hits: Hit[]) => checkHits(good(), capture(hits), { measurementId: 'G-TAGQA0000', typed: TYPED })

test('GA4 hits that mirror the dataLayer pass', () => {
  expect(hitCheck(hitsFor(good()))).toEqual([])
})

test('GA4: uid before sign-in, wrong consent state, items lost, a hit missing', () => {
  const hits = hitsFor(good())
  hits[0].uid = USER
  hits[1].gcs = 'G111'
  hits.find((h) => h.en === 'add_to_cart')!.items = []
  const vs = hitCheck(hits.filter((h) => h.en !== 'sign_up'))
  expect(rulesOf(vs)).toEqual(['ga4-consent', 'ga4-params', 'ga4-sequence', 'ga4-user-id'])
  expect(vs.find((v) => v.rule === 'ga4-user-id')?.message).toBe(`uid "${USER}" while no one is signed in`)
  expect(vs.find((v) => v.rule === 'ga4-consent')?.message).toBe('gcs G111, expected G100 (ad_storage denied, analytics_storage denied at this push)')
})

test('GA4: after a sign-out an empty uid counts as none; the old id does not', () => {
  const rec: Recording = {
    loads: [
      {
        pushes: [
          ['consent', 'default', DENIED],
          { event: 'page_view', page_location: `${ORIGIN}/signin`, page_title: 'Sign in · Tagline Supply' },
          { user_id: USER },
          { event: 'login', method: 'email' },
          { user_id: null },
          { event: 'page_view', page_location: `${ORIGIN}/`, page_title: 'All products · Tagline Supply', page_referrer: `${ORIGIN}/signin` },
        ],
      },
    ],
  }
  const hits = hitsFor(rec)
  expect(hits.map((h) => h.uid)).toEqual([undefined, USER, undefined])
  const run = (last: string | undefined) =>
    checkHits(rec, capture(hits.map((h, i) => (i === 2 ? { ...h, uid: last } : h))), { measurementId: 'G-TAGQA0000' })
  expect(run('')).toEqual([])
  expect(run(undefined)).toEqual([])
  expect(run(USER).map((v) => v.message)).toEqual([`uid "${USER}" while no one is signed in (after a sign-out)`])
})

test('GA4: an email in a request', () => {
  const vs = checkHits(good(), { ...capture(hitsFor(good())), requests: [{ url: 'https://region1.google-analytics.com/g/collect?v=2&dl=x', body: 'en=search&ep.search_term=qa.shopper%40example.com' }] }, { measurementId: 'G-TAGQA0000', typed: TYPED })
  expect(rulesOf(vs)).toEqual(['ga4-pii'])
})

// ---- the plan ------------------------------------------------------------------------

test('the plan covers every event in the contract', () => {
  const planned = new Set(JOURNEYS.flatMap((j) => j.expect.flat()))
  expect([...CONTRACT_EVENTS].filter((e) => !planned.has(e))).toEqual([])
})

test('the plan: unique ids, one expected list per page load, labels the recorder produces', () => {
  expect(new Set(JOURNEYS.map((j) => j.id)).size).toBe(JOURNEYS.length)
  const known = new Set([...CONTRACT_EVENTS, 'consent default', 'consent update granted', 'consent update denied', 'user_id', 'user_id null'])
  for (const j of JOURNEYS) {
    expect(j.steps[0].do, `${j.id} starts with open`).toBe('open')
    expect(j.expect.length, `${j.id}: page loads`).toBe(j.steps.filter((s) => s.do === 'open' || s.do === 'reload').length)
    expect(j.expect.flat().filter((l) => !known.has(l)), `${j.id}: unknown labels`).toEqual([])
    expect(j.expect.every((load) => load[0] === 'consent default'), `${j.id}: every load starts with consent default`).toBe(true)
  }
  expect(sequenceOf(good().loads[1])).toEqual(EXPECT[1])
})
