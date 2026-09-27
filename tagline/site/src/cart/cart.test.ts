import { beforeEach, describe, expect, it, vi } from 'vitest'
import { findProduct, type Product } from '../catalog/catalog'
import type { AddToCartEvent, RemoveFromCartEvent } from '../tagging/types'
import { addProduct, cartCount, emptyCartAfterOrder, getCartLines, MAX_QUANTITY, removeLine, removeProduct } from './cart'

const mug = findProduct('TL-DRK-001') as Product // 13.99

type CartEvent = AddToCartEvent | RemoveFromCartEvent
const cartEvents = () =>
  (globalThis.dataLayer ?? []).filter((e): e is CartEvent => typeof e === 'object' && e !== null && 'event' in e)

describe('cart actions', () => {
  beforeEach(() => {
    emptyCartAfterOrder()
    globalThis.dataLayer = []
  })

  it('tags add_to_cart with the units added and their value', () => {
    addProduct(mug, 2)
    addProduct(mug, 1)
    expect(cartCount(getCartLines())).toBe(3)
    expect(cartEvents().map((e) => [e.event, e.ecommerce.items[0].quantity, e.ecommerce.value])).toEqual([
      ['add_to_cart', 2, 27.98],
      ['add_to_cart', 1, 13.99],
    ])
  })

  it('tags only what was actually added when the line is at its cap', () => {
    addProduct(mug, MAX_QUANTITY - 1)
    expect(addProduct(mug, 5)).toBe(1)
    expect(addProduct(mug, 1)).toBe(0)
    expect(cartEvents().map((e) => e.ecommerce.items[0].quantity)).toEqual([MAX_QUANTITY - 1, 1])
  })

  it('tags remove_from_cart with the units removed, including removing a whole line', () => {
    addProduct(mug, 4)
    removeProduct(mug, 1)
    removeLine(mug)
    expect(getCartLines()).toEqual([])
    expect(cartEvents().map((e) => [e.event, e.ecommerce.items[0].quantity])).toEqual([
      ['add_to_cart', 4],
      ['remove_from_cart', 1],
      ['remove_from_cart', 3],
    ])
  })

  it('does not tag emptying the cart after an order', () => {
    addProduct(mug, 1)
    globalThis.dataLayer = []
    emptyCartAfterOrder()
    expect(globalThis.dataLayer).toEqual([])
  })
})

describe('stored cart', () => {
  it('merges repeated ids, caps each line at MAX_QUANTITY and drops what it cannot use', async () => {
    vi.resetModules()
    const storage = await import('../lib/storage')
    storage.localStore().setItem(
      'tagline.cart',
      JSON.stringify([
        { item_id: 'TL-DRK-001', quantity: 500 },
        { item_id: 'TL-APP-001', quantity: 1 },
        { item_id: 'TL-APP-001', quantity: 2 },
        { item_id: 'TL-XXX-999', quantity: 1 },
        { item_id: 'TL-BAG-001', quantity: 1.5 },
      ]),
    )
    const fresh = await import('./cart')
    expect(fresh.getCartLines().map((l) => [l.product.item_id, l.quantity])).toEqual([
      ['TL-DRK-001', MAX_QUANTITY],
      ['TL-APP-001', 3],
    ])
  })
})
