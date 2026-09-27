import { Link, useSearchParams } from 'react-router'
import { searchProducts } from '../catalog/catalog'
import { ProductGrid } from '../components/ProductGrid'
import { useDocumentTitle, useTrackOnce } from '../hooks'
import { viewItemList } from '../tagging/events'
import { cleanSearchTerm } from '../tagging/pii'

const SEARCH_LIST = { id: 'search_results', name: 'Search results' }

/**
 * The `search` event itself fires when the form is submitted (in the header).
 * This page pushes the results as a view_item_list, or nothing if there are none.
 */
export function SearchResults() {
  const [params] = useSearchParams()
  // Cleaned again here in case the URL was typed or shared rather than submitted.
  const term = cleanSearchTerm(params.get('q') ?? '')
  const results = searchProducts(term)
  useDocumentTitle(term ? `Search: ${term}` : 'Search')
  useTrackOnce(SEARCH_LIST.id, () => (results.length ? viewItemList(SEARCH_LIST, results) : null))

  return (
    <>
      <h1>{term ? `Results for “${term}”` : 'Search'}</h1>
      {results.length > 0 ? (
        <>
          <p className="muted">
            {results.length} {results.length === 1 ? 'product' : 'products'}
          </p>
          <ProductGrid list={SEARCH_LIST} products={results} />
        </>
      ) : (
        <p>
          {term ? 'No products match that search.' : 'Type something in the search box.'}{' '}
          <Link to="/">Browse all products</Link>.
        </p>
      )}
    </>
  )
}
