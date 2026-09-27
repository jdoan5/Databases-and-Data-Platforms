import { createContext, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { getSession, subscribeSession } from './auth/session'
import { getCartLines, subscribeCart } from './cart/cart'
import { consentBannerVisible, subscribeConsent } from './tagging/consent'
import { track } from './tagging/track'
import type { TagEvent } from './tagging/types'

export const useCartLines = () => useSyncExternalStore(subscribeCart, getCartLines)
export const useSession = () => useSyncExternalStore(subscribeSession, getSession)
export const useConsentBannerVisible = () => useSyncExternalStore(subscribeConsent, consentBannerVisible)

/**
 * Sets document.title in a layout effect. Layout effects run child-first, so a page's
 * title is in place before the layout's page_view (also a layout effect) reads it.
 */
export function useDocumentTitle(title: string): void {
  useLayoutEffect(() => {
    document.title = `${title} · Tagline Supply`
  }, [title])
}

/**
 * The current page visit, provided by the layout: it changes when a page_view is sent
 * and at no other time. null outside the layout.
 */
export const PageVisitContext = createContext<string | null>(null)
export const usePageVisit = () => useContext(PageVisitContext)

/**
 * Track an event once per page visit (and per `scope`, e.g. the list or product id).
 * `build` returning null means "nothing to send" (an empty list).
 *
 * Keyed on the page visit, not the router's location.key, so a click on a link to
 * the page already shown sends nothing, just as it sends no page_view. The ref guard
 * makes this safe under React StrictMode, which runs effects twice in development:
 * the second run sees the same key and does nothing. Passive effects run after every
 * layout effect, so these events always follow the page_view.
 */
export function useTrackOnce(scope: string, build: () => TagEvent | null): void {
  const visit = usePageVisit()
  const key = visit === null ? null : `${visit}:${scope}`
  const last = useRef<string | null>(null)
  useEffect(() => {
    if (key === null || last.current === key) return
    last.current = key
    const event = build()
    if (event) track(event)
  })
}
