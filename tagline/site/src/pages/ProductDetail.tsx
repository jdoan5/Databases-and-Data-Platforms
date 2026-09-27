import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { findProduct, formatUSD, type Product } from '../catalog/catalog'
import { addProduct, getCartLines } from '../cart/cart'
import { Swatch } from '../components/ProductGrid'
import { useDocumentTitle, useTrackOnce } from '../hooks'
import { viewItem } from '../tagging/events'
import { NotFound } from './NotFound'

const QUANTITIES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

function Detail({ product }: { product: Product }) {
  const [quantity, setQuantity] = useState(1)
  const [status, setStatus] = useState('')
  useDocumentTitle(product.item_variant ? `${product.item_name}, ${product.item_variant}` : product.item_name)
  useTrackOnce(product.item_id, () => viewItem(product))

  function add() {
    const added = addProduct(product, quantity)
    const inCart = getCartLines().find((l) => l.product.item_id === product.item_id)?.quantity ?? 0
    // The running total makes a second identical add a different message, so the
    // status region announces it too.
    setStatus(added > 0 ? `Added ${added} to your cart (${inCart} in total).` : 'You already have the most we allow of this item.')
  }

  return (
    <article className="product">
      <Swatch product={product} size="large" />
      <div className="product-info">
        <p className="muted">
          <Link to={`/category/${product.item_category.toLowerCase()}`}>{product.item_category}</Link>
        </p>
        <h1>{product.item_name}</h1>
        {product.item_variant && <p className="variant">{product.item_variant}</p>}
        <p className="price">{formatUSD(product.price)}</p>
        <p>{product.description}</p>
        <p className="muted small">Item {product.item_id}</p>
        <div className="add-row">
          <label htmlFor="quantity">Quantity</label>
          <select id="quantity" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))}>
            {QUANTITIES.map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
          <button type="button" className="primary" onClick={add}>
            Add to cart
          </button>
        </div>
        <p role="status" className="status">
          {status && (
            <>
              {status} <Link to="/cart">View cart</Link>
            </>
          )}
        </p>
      </div>
    </article>
  )
}

export function ProductDetail() {
  const { itemId } = useParams()
  const product = findProduct(itemId)
  return product ? <Detail key={product.item_id} product={product} /> : <NotFound />
}
