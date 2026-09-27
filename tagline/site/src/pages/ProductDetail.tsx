import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'
import { findProduct, formatUSD, type Product } from '../catalog/catalog'
import { addProduct } from '../cart/cart'
import { Swatch } from '../components/ProductGrid'
import { useDocumentTitle, useTrackOnce } from '../hooks'
import { viewItem } from '../tagging/events'
import { NotFound } from './NotFound'

const QUANTITIES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

function Detail({ product }: { product: Product }) {
  const location = useLocation()
  const [quantity, setQuantity] = useState(1)
  const [status, setStatus] = useState('')
  useDocumentTitle(product.item_variant ? `${product.item_name}, ${product.item_variant}` : product.item_name)
  useTrackOnce(`${location.key}:${product.item_id}`, () => viewItem(product))

  function add() {
    const added = addProduct(product, quantity)
    setStatus(added > 0 ? `Added ${added} to your cart.` : 'You already have the most we allow of this item.')
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
