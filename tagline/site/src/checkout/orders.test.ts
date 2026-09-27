import { describe, expect, it } from 'vitest'
import { findProduct, type Product } from '../catalog/catalog'
import { localStore } from '../lib/storage'
import { findOrder, placeOrder } from './orders'

const mug = findProduct('TL-DRK-001') as Product

describe('stored orders', () => {
  it('finds an order placed in this browser', () => {
    const order = placeOrder([{ product: mug, quantity: 2 }], 'Ground', 'PayPal')
    expect(findOrder(order.transaction_id)).toEqual(order)
  })

  it('treats an order in an older shape as not found instead of breaking the page', () => {
    // No `lines`: the shape a later change to Order could leave in a reviewer's browser.
    const old = { transaction_id: 'TL-OLD-1', value: 10, tax: 0.8, shipping: 5, total: 15.8, shipping_tier: 'Ground', payment_type: 'PayPal', items: [] }
    localStore().setItem('tagline.orders', JSON.stringify([old]))
    expect(findOrder('TL-OLD-1')).toBeUndefined()
  })
})
