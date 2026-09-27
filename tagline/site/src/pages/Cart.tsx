import { useLayoutEffect, useRef } from 'react'
import { Link } from 'react-router'
import { displayName, formatUSD, type Product } from '../catalog/catalog'
import { addProduct, MAX_QUANTITY, removeLine, removeProduct } from '../cart/cart'
import { Swatch } from '../components/ProductGrid'
import { useCartLines, useDocumentTitle, useTrackOnce } from '../hooks'
import { linesValue, viewCart } from '../tagging/events'

export function Cart() {
  const lines = useCartLines()
  useDocumentTitle('Cart')
  // Once per visit, with the cart as it was on arrival; later quantity changes push
  // add_to_cart / remove_from_cart, not another view_cart.
  useTrackOnce('view_cart', () => (lines.length ? viewCart(lines) : null))

  // Removing a row removes the button that had focus. Focus moves to the next row's
  // Remove button (the previous row's if it was the last), or to the heading once the
  // cart is empty, instead of falling to <body>.
  const heading = useRef<HTMLHeadingElement>(null)
  const refocus = useRef<string | null>(null)
  function removeRow(product: Product, remove: () => void) {
    const i = lines.findIndex((l) => l.product.item_id === product.item_id)
    refocus.current = (lines[i + 1] ?? lines[i - 1])?.product.item_id ?? ''
    remove()
  }
  useLayoutEffect(() => {
    const target = refocus.current
    if (target === null) return
    refocus.current = null
    const next = target ? document.querySelector<HTMLElement>(`[data-remove="${target}"]`) : heading.current
    next?.focus()
  })

  if (lines.length === 0) {
    return (
      <>
        <h1 ref={heading} tabIndex={-1}>
          Cart
        </h1>
        <p>
          Your cart is empty. <Link to="/">Browse the store</Link>.
        </p>
      </>
    )
  }

  return (
    <>
      <h1 ref={heading} tabIndex={-1}>
        Cart
      </h1>
      <ul className="cart-lines">
        {lines.map(({ product, quantity }) => {
          const name = displayName(product)
          return (
            <li key={product.item_id} className="cart-line">
              <Swatch product={product} size="small" />
              <div className="cart-line-name">
                <Link to={`/product/${product.item_id}`}>{product.item_name}</Link>
                {product.item_variant && <span className="muted"> {product.item_variant}</span>}
                <div className="muted small">{formatUSD(product.price)} each</div>
              </div>
              <div className="qty" role="group" aria-label={`Quantity of ${name}`}>
                <button
                  type="button"
                  onClick={() =>
                    quantity === 1 ? removeRow(product, () => removeProduct(product, 1)) : removeProduct(product, 1)
                  }
                  aria-label={`One fewer ${name}`}
                >
                  −
                </button>
                <span aria-live="polite">{quantity}</span>
                <button
                  type="button"
                  onClick={() => addProduct(product, 1)}
                  disabled={quantity >= MAX_QUANTITY}
                  aria-label={`One more ${name}`}
                >
                  +
                </button>
              </div>
              <div className="cart-line-total">{formatUSD(product.price * quantity)}</div>
              <button
                type="button"
                className="link-button"
                data-remove={product.item_id}
                onClick={() => removeRow(product, () => removeLine(product))}
              >
                Remove<span className="visually-hidden"> {name}</span>
              </button>
            </li>
          )
        })}
      </ul>
      <div className="cart-summary">
        <p>
          Subtotal <strong>{formatUSD(linesValue(lines))}</strong>
        </p>
        <p className="muted small">Shipping and tax are added at checkout.</p>
        <Link to="/checkout" className="button primary">
          Checkout
        </Link>
      </div>
    </>
  )
}
