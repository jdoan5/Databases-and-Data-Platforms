/**
 * Event builders: the only way the site constructs an event.
 *
 * Each builder takes domain objects (products, cart lines, an order) and returns an
 * event in the exact shape the contract requires, so page code never assembles a
 * payload by hand. Money is summed in integer cents, so 5 × 19.99 is 99.95 and not
 * 99.94999999999999.
 */
import type { Product } from '../catalog/catalog'
import {
  CURRENCY,
  ITEM_BRAND,
  type AddPaymentInfoEvent,
  type AddShippingInfoEvent,
  type AddToCartEvent,
  type BeginCheckoutEvent,
  type Item,
  type ListItem,
  type LoginEvent,
  type PageViewEvent,
  type PurchaseEvent,
  type RemoveFromCartEvent,
  type SearchEvent,
  type SelectItemEvent,
  type SignUpEvent,
  type ViewCartEvent,
  type ViewItemEvent,
  type ViewItemListEvent,
} from './types'
import { cleanSearchTerm, cleanUrl, redactText } from './pii'

export interface CartLine {
  product: Product
  quantity: number
}

export interface ItemList {
  /** Stable machine id, e.g. "category_drinkware". */
  id: string
  /** Human-readable name, e.g. "Drinkware". */
  name: string
}

// ---- money --------------------------------------------------------------------

export const toCents = (usd: number): number => Math.round(usd * 100)
export const fromCents = (cents: number): number => cents / 100

/** Σ price × quantity, before tax and shipping. The one definition of `value`. */
export function linesValue(lines: readonly CartLine[]): number {
  return fromCents(lines.reduce((sum, l) => sum + toCents(l.product.price) * l.quantity, 0))
}

// ---- items --------------------------------------------------------------------

export function toItem(product: Product, quantity = 1): Item {
  return {
    item_id: product.item_id,
    item_name: product.item_name,
    item_brand: ITEM_BRAND,
    item_category: product.item_category,
    ...(product.item_variant ? { item_variant: product.item_variant } : {}),
    price: product.price,
    quantity,
  }
}

export function toListItem(product: Product, index: number): ListItem {
  return { ...toItem(product, 1), index }
}

const lineItems = (lines: readonly CartLine[]): Item[] => lines.map((l) => toItem(l.product, l.quantity))

function valueEcommerce(lines: readonly CartLine[]) {
  return { currency: CURRENCY, value: linesValue(lines), items: lineItems(lines) }
}

function singleItemEcommerce(product: Product, quantity: number) {
  const line = { product, quantity }
  return { currency: CURRENCY, value: linesValue([line]), items: [toItem(product, quantity)] as [Item] }
}

// ---- builders -----------------------------------------------------------------

/**
 * URLs are cleaned (no fragment, nothing email-shaped) and the three values are cut to
 * GA4's limits. page_referrer is left out, never sent empty, when it is unknown.
 */
export function pageView(p: { page_location: string; page_title: string; page_referrer?: string }): PageViewEvent {
  const location = cleanUrl(p.page_location, 1000) ?? p.page_location.split('#')[0]
  const referrer = p.page_referrer ? cleanUrl(p.page_referrer, 420) : undefined
  const title = redactText(p.page_title).slice(0, 300) || 'Tagline Supply'
  return {
    event: 'page_view',
    page_location: location,
    page_title: title,
    ...(referrer ? { page_referrer: referrer } : {}),
  }
}

export function viewItemList(list: ItemList, products: readonly Product[]): ViewItemListEvent {
  return {
    event: 'view_item_list',
    ecommerce: {
      currency: CURRENCY,
      item_list_id: list.id,
      item_list_name: list.name,
      items: products.map((p, i) => toListItem(p, i)),
    },
  }
}

export function selectItem(list: ItemList, product: Product, index: number): SelectItemEvent {
  return {
    event: 'select_item',
    ecommerce: {
      currency: CURRENCY,
      item_list_id: list.id,
      item_list_name: list.name,
      items: [toListItem(product, index)],
    },
  }
}

export function viewItem(product: Product): ViewItemEvent {
  return { event: 'view_item', ecommerce: singleItemEcommerce(product, 1) }
}

export function addToCart(product: Product, quantity: number): AddToCartEvent {
  return { event: 'add_to_cart', ecommerce: singleItemEcommerce(product, quantity) }
}

export function removeFromCart(product: Product, quantity: number): RemoveFromCartEvent {
  return { event: 'remove_from_cart', ecommerce: singleItemEcommerce(product, quantity) }
}

export function viewCart(lines: readonly CartLine[]): ViewCartEvent {
  return { event: 'view_cart', ecommerce: valueEcommerce(lines) }
}

export function beginCheckout(lines: readonly CartLine[]): BeginCheckoutEvent {
  return { event: 'begin_checkout', ecommerce: valueEcommerce(lines) }
}

export function addShippingInfo(lines: readonly CartLine[], shippingTier: string): AddShippingInfoEvent {
  const { currency, value, items } = valueEcommerce(lines)
  return { event: 'add_shipping_info', ecommerce: { currency, value, shipping_tier: shippingTier, items } }
}

export function addPaymentInfo(lines: readonly CartLine[], paymentType: string): AddPaymentInfoEvent {
  const { currency, value, items } = valueEcommerce(lines)
  return { event: 'add_payment_info', ecommerce: { currency, value, payment_type: paymentType, items } }
}

export interface PurchaseInput {
  transaction_id: string
  lines: readonly CartLine[]
  tax: number
  shipping: number
}

/** `value` excludes tax and shipping; they travel in their own params. */
export function purchase(order: PurchaseInput): PurchaseEvent {
  const { currency, value, items } = valueEcommerce(order.lines)
  return {
    event: 'purchase',
    ecommerce: {
      transaction_id: order.transaction_id,
      currency,
      value,
      tax: order.tax,
      shipping: order.shipping,
      items,
    },
  }
}

/** The term is trimmed, capped at 100 characters, and anything email-shaped becomes [email]. */
export function search(term: string): SearchEvent {
  return { event: 'search', search_term: cleanSearchTerm(term) }
}

export function login(method: string): LoginEvent {
  return { event: 'login', method }
}

export function signUp(method: string): SignUpEvent {
  return { event: 'sign_up', method }
}
