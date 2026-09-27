import { Link } from 'react-router'
import { formatUSD, type Product } from '../catalog/catalog'
import { selectItem, type ItemList } from '../tagging/events'
import { track } from '../tagging/track'

export function Swatch({ product, size = 'card' }: { product: Product; size?: 'card' | 'large' | 'small' }) {
  return <span className={`swatch swatch-${size}`} style={{ background: product.swatch }} aria-hidden="true" />
}

/**
 * A product list. Clicking a product pushes select_item with the list it came from
 * and its position, then the link navigates to the product page.
 */
export function ProductGrid({ list, products }: { list: ItemList; products: readonly Product[] }) {
  return (
    <ul className="grid" aria-label={list.name}>
      {products.map((product, index) => (
        <li key={product.item_id} className="card">
          <Link
            to={`/product/${product.item_id}`}
            onClick={() => track(selectItem(list, product, index))}
            data-item-id={product.item_id}
          >
            <Swatch product={product} />
            <span className="card-name">{product.item_name}</span>
            {product.item_variant && <span className="card-variant">{product.item_variant}</span>}
            <span className="card-price">{formatUSD(product.price)}</span>
          </Link>
        </li>
      ))}
    </ul>
  )
}
