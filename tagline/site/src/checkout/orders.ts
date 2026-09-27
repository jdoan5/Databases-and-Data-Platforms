/**
 * Fake checkout: shipping tiers, payment types, totals, and placed orders kept in
 * localStorage so the confirmation page survives a reload (which is exactly the
 * case the purchase dedupe has to handle).
 */
import { findProduct, type Product } from '../catalog/catalog'
import { localStore, readJSON, writeJSON } from '../lib/storage'
import { fromCents, linesValue, toCents, type CartLine } from '../tagging/events'

export const SHIPPING_TIERS = [
  { id: 'Ground', label: 'Ground, 5 to 7 days', cost: 5 },
  { id: 'Express', label: 'Express, 2 days', cost: 12 },
  { id: 'Next Day', label: 'Next day', cost: 25 },
] as const
export type ShippingTier = (typeof SHIPPING_TIERS)[number]['id']

export const PAYMENT_TYPES = ['Credit Card', 'PayPal', 'Gift Card'] as const
export type PaymentType = (typeof PAYMENT_TYPES)[number]

/** A flat rate so the numbers are easy to check by hand. */
export const TAX_RATE = 0.08

export interface Totals {
  value: number
  tax: number
  shipping: number
  total: number
}

export function totals(lines: readonly CartLine[], tier: ShippingTier | null): Totals {
  const valueCents = toCents(linesValue(lines))
  const taxCents = Math.round(valueCents * TAX_RATE)
  const shippingCents = tier ? toCents(SHIPPING_TIERS.find((t) => t.id === tier)?.cost ?? 0) : 0
  return {
    value: fromCents(valueCents),
    tax: fromCents(taxCents),
    shipping: fromCents(shippingCents),
    total: fromCents(valueCents + taxCents + shippingCents),
  }
}

export interface Order extends Totals {
  transaction_id: string
  placed_at: string
  shipping_tier: ShippingTier
  payment_type: PaymentType
  /** Snapshot of the lines at the moment of purchase, prices included. */
  lines: { product: Product; quantity: number }[]
}

const KEY = 'tagline.orders'
const KEEP = 20

export function newTransactionId(): string {
  const random = crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase().padStart(7, '0')
  return `TL-${Date.now().toString(36).toUpperCase()}-${random}`
}

export function placeOrder(lines: readonly CartLine[], tier: ShippingTier, payment: PaymentType): Order {
  const order: Order = {
    transaction_id: newTransactionId(),
    placed_at: new Date().toISOString(),
    shipping_tier: tier,
    payment_type: payment,
    lines: lines.map((l) => ({ product: { ...l.product }, quantity: l.quantity })),
    ...totals(lines, tier),
  }
  const orders = readJSON<Order[]>(localStore(), KEY, [])
  writeJSON(localStore(), KEY, [...(Array.isArray(orders) ? orders : []), order].slice(-KEEP))
  return order
}

const isMoney = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0

/**
 * A stored order is checked before it is shown or tagged, like the cart and the
 * session: one written by an older build, or edited by hand, reads as "not found"
 * rather than breaking the page.
 */
function isOrder(o: unknown): o is Order {
  if (typeof o !== 'object' || o === null) return false
  const r = o as Record<string, unknown>
  return (
    typeof r.transaction_id === 'string' &&
    SHIPPING_TIERS.some((t) => t.id === r.shipping_tier) &&
    (PAYMENT_TYPES as readonly unknown[]).includes(r.payment_type) &&
    [r.value, r.tax, r.shipping, r.total].every(isMoney) &&
    Array.isArray(r.lines) &&
    r.lines.length > 0 &&
    r.lines.every((l: { product?: Partial<Product>; quantity?: unknown }) => {
      const p = l?.product
      return (
        typeof p?.item_id === 'string' &&
        !!findProduct(p.item_id) &&
        typeof p.item_name === 'string' &&
        isMoney(p.price) &&
        p.price > 0 &&
        Number.isInteger(l.quantity) &&
        (l.quantity as number) > 0
      )
    })
  )
}

export function findOrder(transactionId: string | undefined): Order | undefined {
  if (!transactionId) return undefined
  const orders = readJSON<unknown>(localStore(), KEY, [])
  const order = Array.isArray(orders) ? orders.find((o) => o?.transaction_id === transactionId) : undefined
  return isOrder(order) ? order : undefined
}
