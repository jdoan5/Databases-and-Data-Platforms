/**
 * The event contract, as TypeScript types.
 *
 * These mirror tagging/events.schema.json (the machine-readable contract) and follow
 * Google's GA4 recommended events and the GTM "measure ecommerce" dataLayer format.
 * The types stop most mistakes at compile time; the schema catches the rest at runtime.
 */
import type { Category } from '../catalog/catalog'

export const CURRENCY = 'USD' as const
export const ITEM_BRAND = 'Tagline Supply' as const

export interface Item {
  item_id: string
  item_name: string
  item_brand: typeof ITEM_BRAND
  item_category: Category
  item_variant?: string
  price: number
  quantity: number
  /** Position in the list, from 0. Only in list contexts (view_item_list, select_item). */
  index?: number
}

export type ListItem = Item & { index: number }

interface ValueEcommerce<I extends Item[] = Item[]> {
  currency: typeof CURRENCY
  /** Σ price × quantity over items, before tax and shipping. */
  value: number
  items: I
}

interface ListEcommerce<I extends ListItem[]> {
  currency: typeof CURRENCY
  item_list_id: string
  item_list_name: string
  items: I
}

export interface PageViewEvent {
  event: 'page_view'
  page_location: string
  page_title: string
  page_referrer?: string
}

export interface ViewItemListEvent {
  event: 'view_item_list'
  ecommerce: ListEcommerce<ListItem[]>
}

export interface SelectItemEvent {
  event: 'select_item'
  ecommerce: ListEcommerce<[ListItem]>
}

export interface ViewItemEvent {
  event: 'view_item'
  ecommerce: ValueEcommerce<[Item]>
}

export interface AddToCartEvent {
  event: 'add_to_cart'
  ecommerce: ValueEcommerce<[Item]>
}

export interface RemoveFromCartEvent {
  event: 'remove_from_cart'
  ecommerce: ValueEcommerce<[Item]>
}

export interface ViewCartEvent {
  event: 'view_cart'
  ecommerce: ValueEcommerce
}

export interface BeginCheckoutEvent {
  event: 'begin_checkout'
  ecommerce: ValueEcommerce
}

export interface AddShippingInfoEvent {
  event: 'add_shipping_info'
  ecommerce: ValueEcommerce & { shipping_tier: string }
}

export interface AddPaymentInfoEvent {
  event: 'add_payment_info'
  ecommerce: ValueEcommerce & { payment_type: string }
}

export interface PurchaseEvent {
  event: 'purchase'
  ecommerce: ValueEcommerce & { transaction_id: string; tax: number; shipping: number }
}

export interface SearchEvent {
  event: 'search'
  search_term: string
}

export interface LoginEvent {
  event: 'login'
  method: string
}

export interface SignUpEvent {
  event: 'sign_up'
  method: string
}

export type EcommerceEvent =
  | ViewItemListEvent
  | SelectItemEvent
  | ViewItemEvent
  | AddToCartEvent
  | RemoveFromCartEvent
  | ViewCartEvent
  | BeginCheckoutEvent
  | AddShippingInfoEvent
  | AddPaymentInfoEvent
  | PurchaseEvent

export type TagEvent = PageViewEvent | EcommerceEvent | SearchEvent | LoginEvent | SignUpEvent

export type EventName = TagEvent['event']

export function isEcommerceEvent(e: TagEvent): e is EcommerceEvent {
  return 'ecommerce' in e
}
