import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavigationType, Outlet, useLocation, useNavigate, useNavigationType, useSearchParams } from 'react-router'
import { signOut } from '../auth/session'
import { cartCount } from '../cart/cart'
import { PageVisitContext, useCartLines, useSession } from '../hooks'
import { openConsentSettings } from '../tagging/consent'
import { pageView, search } from '../tagging/events'
import { cleanSearchTerm } from '../tagging/pii'
import { track } from '../tagging/track'
import { ConsentBanner } from './ConsentBanner'
import { TagInspector } from './TagInspector'

/**
 * One id per page visit, for page_view and every once-per-page event.
 *
 * Normally the router's location.key: a new history entry, Back/Forward, or a replace
 * to a new URL is a new page. The exception: react-router's <Link> turns a click on a
 * link to the current URL (the brand on home, the active category, Cart on the cart
 * page) into a replace with a fresh key. The URL has not changed, so the visit, and
 * its id, stay the same. Worked out during render (React's "adjust state when a prop
 * changes" pattern) so the pages below see the right id on their first render.
 */
function usePageVisit(): string {
  const location = useLocation()
  const navigationType = useNavigationType()
  const url = location.pathname + location.search
  const [visit, setVisit] = useState({ key: location.key, url, id: location.key })
  if (visit.key === location.key) return visit.id
  const id = navigationType === NavigationType.Replace && url === visit.url ? visit.id : location.key
  setVisit({ key: location.key, url, id })
  return id
}

/**
 * page_view once per page visit.
 *
 * A layout effect, so it runs after the new page's own layout effects (which set
 * document.title) and before any page's passive effects (which push view_item_list,
 * view_item and so on). Keyed on the visit id, so StrictMode's second effect run is
 * not a new page. page_referrer is the previous in-app URL, or document.referrer for
 * the first page when the browser provides one.
 */
function usePageViews(visit: string): void {
  const lastVisit = useRef<string | null>(null)
  const previousUrl = useRef<string>(typeof document === 'undefined' ? '' : document.referrer)
  useLayoutEffect(() => {
    if (lastVisit.current === visit) return
    lastVisit.current = visit
    const url = window.location.href
    track(pageView({ page_location: url, page_title: document.title, page_referrer: previousUrl.current || undefined }))
    previousUrl.current = url
  }, [visit])
}

/**
 * The link or button that started a navigation often leaves with the old page, and
 * focus falls to <body>: a keyboard or screen-reader user would start again from the
 * top of the document. In that case focus moves to <main>. Focus that survived (the
 * search box, a header link) is left where it is, and the first load is left alone.
 */
function useFocusAfterNavigation(visit: string): void {
  const lastVisit = useRef<string | null>(null)
  useEffect(() => {
    const previous = lastVisit.current
    lastVisit.current = visit
    if (previous === null || previous === visit) return
    const active = document.activeElement
    if (!active || active === document.body) document.getElementById('main')?.focus({ preventScroll: true })
  }, [visit])
}

function SearchForm() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const location = useLocation()
  const current = location.pathname === '/search' ? (params.get('q') ?? '') : ''
  const input = useRef<HTMLInputElement>(null)

  // The box shows the current term. It is updated in place rather than remounted, so
  // focus stays in the box after a submit.
  useEffect(() => {
    if (input.current) input.current.value = current
  }, [current])

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
      <input
        ref={input}
        id="site-search"
        name="q"
        type="search"
        defaultValue={current}
        placeholder="Search products"
        required
      />
      <button type="submit">Search</button>
    </form>
  )
}

export function Layout({ debug }: { debug: boolean }) {
  const visit = usePageVisit()
  usePageViews(visit)
  useFocusAfterNavigation(visit)
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
              Cart{' '}
              <span className="count" aria-hidden="true">
                {count}
              </span>
              <span className="visually-hidden">({count === 1 ? '1 item' : `${count} items`})</span>
            </Link>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        <PageVisitContext value={visit}>
          <Outlet />
        </PageVisitContext>
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
