import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { displayName, formatUSD } from '../catalog/catalog'
import { findOrder, type Order } from '../checkout/orders'
import { useDocumentTitle } from '../hooks'
import { purchase } from '../tagging/events'
import { claimTransaction, isTransactionClaimed } from '../tagging/purchaseDedupe'
import { track } from '../tagging/track'

/**
 * purchase fires here, once per transaction_id. claimTransaction() records the id in
 * localStorage before the push, so a reload, a Back into this page or a second tab
 * finds it already claimed and pushes nothing. The ref stops StrictMode's second
 * effect run from even asking.
 *
 * Returns whether the event had already been pushed before this visit, read once
 * before the effect runs, for the note under the receipt.
 */
function usePurchaseOnce(order: Order): boolean {
  const [sentBefore] = useState(() => isTransactionClaimed(order.transaction_id))
  const handled = useRef<string | null>(null)
  useEffect(() => {
    if (handled.current === order.transaction_id) return
    handled.current = order.transaction_id
    if (claimTransaction(order.transaction_id)) {
      track(purchase({ transaction_id: order.transaction_id, lines: order.lines, tax: order.tax, shipping: order.shipping }))
    }
  }, [order])
  return sentBefore
}

function Receipt({ order }: { order: Order }) {
  const sentBefore = usePurchaseOnce(order)
  return (
    <>
      <h1>Order confirmed</h1>
      <p>
        Thanks. Order <strong data-testid="transaction-id">{order.transaction_id}</strong> is confirmed. Nothing will
        ship: this is a demo store.
      </p>
      <section className="order-summary" aria-labelledby="receipt-title">
        <h2 id="receipt-title">Receipt</h2>
        <ul>
          {order.lines.map(({ product, quantity }) => (
            <li key={product.item_id}>
              <span>
                {quantity} × {displayName(product)}
              </span>
              <span>{formatUSD(product.price * quantity)}</span>
            </li>
          ))}
        </ul>
        <dl>
          <dt>Subtotal</dt>
          <dd>{formatUSD(order.value)}</dd>
          <dt>Shipping ({order.shipping_tier})</dt>
          <dd>{formatUSD(order.shipping)}</dd>
          <dt>Tax</dt>
          <dd>{formatUSD(order.tax)}</dd>
          <dt className="total">Total</dt>
          <dd className="total">{formatUSD(order.total)}</dd>
        </dl>
        <p className="muted small">Paid with: {order.payment_type} (not really).</p>
      </section>
      <p className="muted small" data-testid="purchase-status">
        {sentBefore
          ? 'The purchase event for this order was pushed on an earlier visit, so it was not pushed again.'
          : 'The purchase event was pushed for this order. Reloading this page will not push it again.'}
      </p>
      <p>
        <Link to="/">Keep shopping</Link>
      </p>
    </>
  )
}

export function Confirmation() {
  const { transactionId } = useParams()
  // Parsed once per id, so the receipt's effect sees a stable object.
  const order = useMemo(() => findOrder(transactionId), [transactionId])
  useDocumentTitle(order ? 'Order confirmed' : 'Order not found')
  if (!order) {
    return (
      <>
        <h1>Order not found</h1>
        <p>
          There is no order with that number in this browser. <Link to="/">Back to the store</Link>.
        </p>
      </>
    )
  }
  return <Receipt key={order.transaction_id} order={order} />
}
