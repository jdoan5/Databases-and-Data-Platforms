import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { displayName, formatUSD } from '../catalog/catalog'
import { emptyCartAfterOrder } from '../cart/cart'
import {
  PAYMENT_TYPES,
  placeOrder,
  SHIPPING_TIERS,
  TAX_RATE,
  totals,
  type PaymentType,
  type ShippingTier,
} from '../checkout/orders'
import { useCartLines, useDocumentTitle, useTrackOnce } from '../hooks'
import { addPaymentInfo, addShippingInfo, beginCheckout } from '../tagging/events'
import { track } from '../tagging/track'

const isTier = (v: string): v is ShippingTier => SHIPPING_TIERS.some((t) => t.id === v)
const isPayment = (v: string): v is PaymentType => (PAYMENT_TYPES as readonly string[]).includes(v)

export function Checkout() {
  const location = useLocation()
  const navigate = useNavigate()
  const lines = useCartLines()
  const [tier, setTier] = useState<ShippingTier | null>(null)
  const [payment, setPayment] = useState<PaymentType | null>(null)
  useDocumentTitle('Checkout')
  useTrackOnce(location.key, () => (lines.length ? beginCheckout(lines) : null))

  if (lines.length === 0) {
    return (
      <>
        <h1>Checkout</h1>
        <p>
          Your cart is empty. <Link to="/">Browse the store</Link>.
        </p>
      </>
    )
  }

  const t = totals(lines, tier)

  // Only a real change is tagged: re-picking the current option is not a new choice.
  function chooseTier(value: string) {
    if (!isTier(value) || value === tier) return
    setTier(value)
    track(addShippingInfo(lines, value))
  }

  function choosePayment(value: string) {
    if (!isPayment(value) || value === payment) return
    setPayment(value)
    track(addPaymentInfo(lines, value))
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!tier || !payment) return
    const order = placeOrder(lines, tier, payment)
    emptyCartAfterOrder()
    // replace: Back from the confirmation page should not land on an empty checkout.
    navigate(`/order/${order.transaction_id}`, { replace: true })
  }

  return (
    <>
      <h1>Checkout</h1>
      <form className="checkout" onSubmit={onSubmit}>
        <fieldset>
          <legend>Delivery</legend>
          <label htmlFor="shipping">Shipping</label>
          <select id="shipping" value={tier ?? ''} onChange={(e) => chooseTier(e.target.value)} required>
            <option value="" disabled>
              Choose shipping
            </option>
            {SHIPPING_TIERS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label} ({formatUSD(s.cost)})
              </option>
            ))}
          </select>
        </fieldset>

        <fieldset>
          <legend>Payment</legend>
          <label htmlFor="payment">Payment type</label>
          <select id="payment" value={payment ?? ''} onChange={(e) => choosePayment(e.target.value)} required>
            <option value="" disabled>
              Choose payment type
            </option>
            {PAYMENT_TYPES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <p className="muted small">This is a demo: no card number or account is asked for, and nothing is charged.</p>
        </fieldset>

        <section className="order-summary" aria-labelledby="summary-title">
          <h2 id="summary-title">Order summary</h2>
          <ul>
            {lines.map(({ product, quantity }) => (
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
            <dd>{formatUSD(t.value)}</dd>
            <dt>Shipping</dt>
            <dd>{tier ? formatUSD(t.shipping) : '—'}</dd>
            <dt>Tax ({Math.round(TAX_RATE * 100)}%)</dt>
            <dd>{formatUSD(t.tax)}</dd>
            <dt className="total">Total</dt>
            <dd className="total">{formatUSD(t.total)}</dd>
          </dl>
        </section>

        <button type="submit" className="primary" disabled={!tier || !payment}>
          Place order
        </button>
      </form>
    </>
  )
}
