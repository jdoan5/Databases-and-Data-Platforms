import { useEffect, useLayoutEffect, useRef, type FormEvent } from 'react'
import { Link, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router'
import { signOut } from '../auth/session'
import { cartCount } from '../cart/cart'
import { useCartLines, useSession } from '../hooks'
import { openConsentSettings } from '../tagging/consent'
import { pageView, search } from '../tagging/events'
import { cleanSearchTerm } from '../tagging/pii'
import { track } from '../tagging/track'
import { ConsentBanner } from './ConsentBanner'
import { TagInspector } from './TagInspector'

/**
 * page_view once per route change.
 *
 * A layout effect, so it runs after the new page's own layout effects (which set
 * document.title) and before any page's passive effects (which push view_item_list,
 * view_item and so on). Keyed on location.key: a new history entry is a new page,
 * and StrictMode's second effect run is not. page_referrer is the previous in-app
 * URL, or document.referrer for the first page when the browser provides one.
 */
function usePageViews(): void {
  const location = useLocation()
  const lastKey = useRef<string | null>(null)
  const previousUrl = useRef<string>(typeof document === 'undefined' ? '' : document.referrer)
  useLayoutEffect(() => {
    if (lastKey.current === location.key) return
    lastKey.current = location.key
    const url = window.location.href
    track(pageView({ page_location: url, page_title: document.title, page_referrer: previousUrl.current || undefined }))
    previousUrl.current = url
  }, [location.key])
}

function SearchForm() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const location = useLocation()
  const current = location.pathname === '/search' ? (params.get('q') ?? '') : ''

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    // Cleaned once and used for both the event and the URL, so an email typed into the
    // box never reaches search_term, page_location or page_title.
    const term = cleanSearchTerm(String(new FormData(e.currentTarget).get('q') ?? ''))
    if (!term) return
    track(search(term))
    navigate(`/search?q=${encodeURIComponent(term)}`)
  }

  return (
    <form role="search" className="search" onSubmit={onSubmit}>
      <label htmlFor="site-search" className="visually-hidden">
        Search products
      </label>
      <input id="site-search" key={current} name="q" type="search" defaultValue={current} placeholder="Search products" required />
      <button type="submit">Search</button>
    </form>
  )
}

export function Layout({ debug }: { debug: boolean }) {
  usePageViews()
  const location = useLocation()
  const lines = useCartLines()
  const session = useSession()
  const count = cartCount(lines)

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  return (
    <>
      {/* Focus moves without touching the URL: a #main hash would be a history entry,
          and so an extra page_view. */}
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault()
          document.getElementById('main')?.focus()
        }}
      >
        Skip to content
      </a>
      <header className="site-header">
        <div className="bar">
          <Link to="/" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            Tagline Supply
          </Link>
          <SearchForm />
          <nav aria-label="Account and cart" className="account">
            {session ? (
              <button type="button" className="link-button" onClick={signOut}>
                Sign out
              </button>
            ) : (
              <Link to="/signin">Sign in</Link>
            )}
            <Link to="/cart" className="cart-link">
              Cart <span className="count" aria-label={`${count} items`}>{count}</span>
            </Link>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>

      <footer className="site-footer">
        <p>
          A demo store for a tagging project. Nothing is for sale, nothing ships, and no payment details are ever asked
          for.
        </p>
        <button type="button" className="link-button" onClick={openConsentSettings}>
          Cookie settings
        </button>
      </footer>

      <ConsentBanner />
      {debug && <TagInspector />}
    </>
  )
}
