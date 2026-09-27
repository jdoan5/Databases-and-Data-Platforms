import { NavLink, useLocation, useParams } from 'react-router'
import { CATEGORIES, categoryFromSlug, categorySlug, products, productsInCategory, type Product } from '../catalog/catalog'
import { ProductGrid } from '../components/ProductGrid'
import { useDocumentTitle, useTrackOnce } from '../hooks'
import { viewItemList, type ItemList } from '../tagging/events'
import { NotFound } from './NotFound'

function CategoryNav() {
  return (
    <nav aria-label="Categories" className="categories">
      <NavLink to="/" end>
        All
      </NavLink>
      {CATEGORIES.map((c) => (
        <NavLink key={c} to={`/category/${categorySlug(c)}`}>
          {c}
        </NavLink>
      ))}
    </nav>
  )
}

function Listing({ heading, list, items }: { heading: string; list: ItemList; items: readonly Product[] }) {
  const location = useLocation()
  useDocumentTitle(heading)
  useTrackOnce(`${location.key}:${list.id}`, () => viewItemList(list, items))
  return (
    <>
      <h1>{heading}</h1>
      <CategoryNav />
      <ProductGrid list={list} products={items} />
    </>
  )
}

/** Home (all products) and the category pages share one component. */
export function Home() {
  const { slug } = useParams()
  if (slug === undefined) {
    return <Listing heading="All products" list={{ id: 'all_products', name: 'All products' }} items={products} />
  }
  const category = categoryFromSlug(slug)
  if (!category) return <NotFound />
  return (
    <Listing
      key={category}
      heading={category}
      list={{ id: `category_${categorySlug(category)}`, name: category }}
      items={productsInCategory(category)}
    />
  )
}
