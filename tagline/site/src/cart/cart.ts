/**
 * The cart: `[{ item_id, quantity }]` in localStorage, exposed as an external store
 * for useSyncExternalStore. Products are looked up from the catalog at read time, so
 * the stored cart never carries stale names or prices.
 *
 * The actions tag themselves: adding pushes add_to_cart and removing pushes
 * remove_from_cart for the quantity that actually changed. Emptying the cart after
 * an order is not a removal and pushes nothing.
 */
import { findProduct, type Product } from '../catalog/catalog'
import { localStore, readJSON, writeJSON } from '../lib/storage'
import { addToCart, removeFromCart, type CartLine } from '../tagging/events'
import { track } from '../tagging/track'

export interface StoredLine {
  item_id: string
  quantity: number
}

const KEY = 'tagline.cart'
export const MAX_QUANTITY = 20

function load(): readonly StoredLine[] {
  const raw = readJSON<unknown>(localStore(), KEY, [])
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (l): l is StoredLine =>
      typeof l?.item_id === 'string' && Number.isInteger(l?.quantity) && l.quantity > 0 && !!findProduct(l.item_id),
  )
}

let stored: readonly StoredLine[] = load()
let lines: readonly CartLine[] = resolve(stored)
const listeners = new Set<() => void>()

function resolve(s: readonly StoredLine[]): CartLine[] {
  return s.flatMap((l) => {
    const product = findProduct(l.item_id)
    return product ? [{ product, quantity: l.quantity }] : []
  })
}

function commit(next: readonly StoredLine[]): void {
  stored = next
  lines = resolve(next)
  writeJSON(localStore(), KEY, next)
  listeners.forEach((l) => l())
}

// Keep tabs in sync.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) {
      stored = load()
      lines = resolve(stored)
      listeners.forEach((l) => l())
    }
  })
}

export const getCartLines = (): readonly CartLine[] => lines

export function subscribeCart(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

export const cartCount = (ls: readonly CartLine[]): number => ls.reduce((n, l) => n + l.quantity, 0)

const quantityOf = (itemId: string) => stored.find((l) => l.item_id === itemId)?.quantity ?? 0

function setQuantity(itemId: string, quantity: number): void {
  const others = stored.filter((l) => l.item_id !== itemId)
  if (quantity <= 0) {
    commit(others)
    return
  }
  const exists = stored.some((l) => l.item_id === itemId)
  commit(
    exists
      ? stored.map((l) => (l.item_id === itemId ? { item_id: itemId, quantity } : l))
      : [...stored, { item_id: itemId, quantity }],
  )
}

/** Adds up to MAX_QUANTITY per line; tags only what was actually added. */
export function addProduct(product: Product, quantity: number): number {
  const before = quantityOf(product.item_id)
  const after = Math.min(MAX_QUANTITY, before + Math.max(0, Math.floor(quantity)))
  const added = after - before
  if (added > 0) {
    setQuantity(product.item_id, after)
    track(addToCart(product, added))
  }
  return added
}

export function removeProduct(product: Product, quantity: number): number {
  const before = quantityOf(product.item_id)
  const removed = Math.min(before, Math.max(0, Math.floor(quantity)))
  if (removed > 0) {
    setQuantity(product.item_id, before - removed)
    track(removeFromCart(product, removed))
  }
  return removed
}

export function removeLine(product: Product): number {
  return removeProduct(product, quantityOf(product.item_id))
}

/** After an order is placed. Not a remove_from_cart. */
export function emptyCartAfterOrder(): void {
  commit([])
}
