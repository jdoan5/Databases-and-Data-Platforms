import { Link, useLocation } from 'react-router'
import { displayName, formatUSD } from '../catalog/catalog'
import { addProduct, MAX_QUANTITY, removeLine, removeProduct } from '../cart/cart'
import { Swatch } from '../components/ProductGrid'
import { useCartLines, useDocumentTitle, useTrackOnce } from '../hooks'
import { linesValue, viewCart } from '../tagging/events'

export function Cart() {
  const location = useLocation()
  const lines = useCartLines()
  useDocumentTitle('Cart')
  // Once per visit, with the cart as it was on arrival; later quantity changes push
  // add_to_cart / remove_from_cart, not another view_cart.
  useTrackOnce(location.key, () => (lines.length ? viewCart(lines) : null))

  if (lines.length === 0) {
    return (
      <>
        <h1>Cart</h1>
        <p>
          Your cart is empty. <Link to="/">Browse the store</Link>.
        </p>
      </>
    )
  }

  return (
    <>
      <h1>Cart</h1>
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
                <button type="button" onClick={() => removeProduct(product, 1)} aria-label={`One fewer ${name}`}>
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
              <button type="button" className="link-button" onClick={() => removeLine(product)}>
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
