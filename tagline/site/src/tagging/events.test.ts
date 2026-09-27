import { beforeEach, describe, expect, it } from 'vitest'
import schema from '../../../tagging/events.schema.json'
import { findProduct, products, type Product } from '../catalog/catalog'
import { totals } from '../checkout/orders'
import { createContract, type JsonSchema } from './contract'
import * as build from './events'
import { track } from './track'
import type { TagEvent } from './types'

// strict: true, so a schema that Ajv only half understands fails here rather than
// silently validating less than it claims to.
const contract = createContract(schema as JsonSchema, { strict: true })

const product = (id: string): Product => {
  const p = findProduct(id)
  if (!p) throw new Error(`no product ${id}`)
  return p
}

const tee = product('TL-APP-001') // 24.00, has a variant
const tote = product('TL-BAG-001') // 19.99
const pens = product('TL-OFF-002') // 8.50, no variant
const lines = [
  { product: tote, quantity: 3 },
  { product: pens, quantity: 2 },
]
const list = { id: 'category_bags', name: 'Bags' }

const cases: [string, TagEvent][] = [
  ['page_view', build.pageView({ page_location: 'http://localhost:5173/', page_title: 'All products · Tagline Supply' })],
  [
    'page_view',
    build.pageView({
      page_location: 'http://localhost:5173/cart',
      page_title: 'Cart · Tagline Supply',
      page_referrer: 'http://localhost:5173/product/TL-BAG-001',
    }),
  ],
  ['view_item_list', build.viewItemList(list, products.filter((p) => p.item_category === 'Bags'))],
  ['select_item', build.selectItem(list, tote, 0)],
  ['view_item', build.viewItem(tee)],
  ['add_to_cart', build.addToCart(tote, 3)],
  ['remove_from_cart', build.removeFromCart(pens, 1)],
  ['view_cart', build.viewCart(lines)],
  ['begin_checkout', build.beginCheckout(lines)],
  ['add_shipping_info', build.addShippingInfo(lines, 'Express')],
  ['add_payment_info', build.addPaymentInfo(lines, 'Credit Card')],
  ['purchase', build.purchase({ transaction_id: 'TL-MG3K2ZQ1-0A7F3KD', lines, tax: 5.62, shipping: 12 })],
  ['search', build.search('  enamel mug ')],
  ['login', build.login('email')],
  ['sign_up', build.signUp('email')],
]

describe('event builders', () => {
  it.each(cases)('%s validates against its $defs entry', (name, event) => {
    const result = contract.check(event)
    expect(result.errors).toEqual([])
    expect(result).toMatchObject({ kind: 'event', label: name, valid: true, rule: `$defs/${name}` })
  })

  it('covers every event the contract defines', () => {
    const defined = (schema as { $defs: { event_name: { enum: string[] } } }).$defs.event_name.enum
    expect(new Set(cases.map(([name]) => name))).toEqual(new Set(defined))
  })

  it('builds GA4 items with brand, category, variant only when present, and list index', () => {
    const [item] = build.selectItem(list, tee, 4).ecommerce.items
    expect(item).toEqual({
      item_id: 'TL-APP-001',
      item_name: 'Classic Logo Tee',
      item_brand: 'Tagline Supply',
      item_category: 'Apparel',
      item_variant: 'Navy',
      price: 24,
      quantity: 1,
      index: 4,
    })
    expect(build.viewItem(pens).ecommerce.items[0]).not.toHaveProperty('item_variant')
    expect(build.viewItem(pens).ecommerce.items[0]).not.toHaveProperty('index')
  })

  it('numbers list items from 0 in display order', () => {
    const event = build.viewItemList(list, [tote, pens, tee])
    expect(event.ecommerce.items.map((i) => [i.item_id, i.index])).toEqual([
      ['TL-BAG-001', 0],
      ['TL-OFF-002', 1],
      ['TL-APP-001', 2],
    ])
  })
})

describe('value math', () => {
  it('sums in cents, so 5 × 19.99 is exactly 99.95', () => {
    expect(5 * 19.99).toBe(99.94999999999999) // the float trap this avoids
    expect(build.addToCart(tote, 5).ecommerce.value).toBe(99.95)
    expect(build.addToCart(tote, 3).ecommerce.value).toBe(59.97)
  })

  it('is Σ price × quantity for multi-line events', () => {
    // 3 × 19.99 + 2 × 8.50 = 59.97 + 17.00
    for (const event of [build.viewCart(lines), build.beginCheckout(lines), build.addShippingInfo(lines, 'Ground')]) {
      expect(event.ecommerce.value).toBe(76.97)
    }
  })

  it('is the same number in every funnel event for the same cart', () => {
    const values = [
      build.viewCart(lines),
      build.beginCheckout(lines),
      build.addShippingInfo(lines, 'Next Day'),
      build.addPaymentInfo(lines, 'PayPal'),
      build.purchase({ transaction_id: 'TL-A-B', lines, tax: 0, shipping: 0 }),
    ].map((e) => e.ecommerce.value)
    expect(new Set(values)).toEqual(new Set([76.97]))
  })

  it('keeps tax and shipping out of the purchase value', () => {
    const t = totals(lines, 'Express')
    expect(t).toEqual({ value: 76.97, tax: 6.16, shipping: 12, total: 95.13 })
    const event = build.purchase({ transaction_id: 'TL-A-B', lines, tax: t.tax, shipping: t.shipping })
    expect(event.ecommerce).toMatchObject({ value: 76.97, tax: 6.16, shipping: 12 })
  })

  it('uses the units changed, not the new line total, on add_to_cart and remove_from_cart', () => {
    expect(build.addToCart(pens, 2).ecommerce).toMatchObject({ value: 17, items: [{ quantity: 2 }] })
    expect(build.removeFromCart(pens, 1).ecommerce).toMatchObject({ value: 8.5, items: [{ quantity: 1 }] })
  })
})

describe('track()', () => {
  beforeEach(() => {
    globalThis.dataLayer = []
  })

  it('pushes { ecommerce: null } immediately before every ecommerce event, and not before others', () => {
    track(build.search('mug'))
    track(build.viewItem(tee))
    track(build.addToCart(tee, 1))
    track(build.login('email'))
    expect(globalThis.dataLayer).toEqual([
      build.search('mug'),
      { ecommerce: null },
      build.viewItem(tee),
      { ecommerce: null },
      build.addToCart(tee, 1),
      build.login('email'),
    ])
    for (const entry of globalThis.dataLayer ?? []) expect(contract.check(entry).valid).toBe(true)
  })
})

describe('the contract rejects what it should', () => {
  const bad: [string, unknown][] = [
    ['value as a string', { ...build.viewItem(tee), ecommerce: { ...build.viewItem(tee).ecommerce, value: '24.00' } }],
    ['missing currency', { event: 'view_cart', ecommerce: { value: 1, items: build.viewCart(lines).ecommerce.items } }],
    ['a stray key', { ...build.login('email'), user_email: 'x' }],
    ['an unknown event', { event: 'add_to_wishlist' }],
    ['an empty items array', { event: 'view_cart', ecommerce: { currency: 'USD', value: 1, items: [] } }],
    ['a list item without index', { event: 'select_item', ecommerce: { ...build.selectItem(list, tee, 0).ecommerce, items: [build.toItem(tee)] } }],
    ['an email in search_term', { event: 'search', search_term: 'jo@example.com' }],
    ['a non-hex user_id', { user_id: 'jo@example.com' }],
    ['an ecommerce object without an event', { ecommerce: { currency: 'USD' } }],
  ]
  it.each(bad)('rejects %s', (_label, entry) => {
    const result = contract.check(entry)
    expect(result.valid).toBe(false)
    expect(result.errors.length).toBeGreaterThan(0)
  })
})

describe('free text never carries an email', () => {
  it('redacts an email typed into search, and trims and caps the term', () => {
    expect(build.search('  gift for jo.doan+test@example.com  ').search_term).toBe('gift for [email]')
    expect(build.search('x'.repeat(150)).search_term).toHaveLength(100)
  })

  it('cleans page_view URLs and titles', () => {
    const event = build.pageView({
      page_location: 'http://localhost:5173/search?q=jo%40example.com#main',
      page_title: 'Search: jo@example.com · Tagline Supply',
      page_referrer: '',
    })
    expect(event).toEqual({
      event: 'page_view',
      page_location: 'http://localhost:5173/search?q=%5Bemail%5D',
      page_title: 'Search: [email] · Tagline Supply',
    })
    expect(contract.check(event).valid).toBe(true)
  })

  it('leaves ordinary URLs exactly as they were, minus the fragment', () => {
    const event = build.pageView({ page_location: 'http://localhost:5173/search?q=blue%20mug#top', page_title: 't' })
    expect(event.page_location).toBe('http://localhost:5173/search?q=blue%20mug')
  })

  it('drops an unusable referrer rather than sending it', () => {
    expect(build.pageView({ page_location: 'http://localhost:5173/', page_title: 't', page_referrer: 'android-app://x' })).not.toHaveProperty(
      'page_referrer',
    )
  })
})

describe('catalog', () => {
  it('has ~20 products in the 5 categories, with contract-shaped ids and prices', () => {
    expect(products.length).toBeGreaterThanOrEqual(18)
    expect(new Set(products.map((p) => p.item_category))).toEqual(
      new Set(['Apparel', 'Drinkware', 'Bags', 'Office', 'Stickers']),
    )
    expect(new Set(products.map((p) => p.item_id)).size).toBe(products.length)
    for (const p of products) {
      expect(p.item_id).toMatch(/^TL-[A-Z]{3}-[0-9]{3}$/)
      expect(Math.round(p.price * 100) / 100).toBe(p.price)
      expect(contract.check(build.viewItem(p)).valid).toBe(true)
    }
  })
})
