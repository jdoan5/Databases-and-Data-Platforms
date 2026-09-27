import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
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
 * Track an event once per `key` (normally the router's location.key, so once per
 * page visit). `build` returning null means "nothing to send" (an empty list).
 *
 * The ref guard makes this safe under React StrictMode, which runs effects twice in
 * development: the second run sees the same key and does nothing. Passive effects
 * run after every layout effect, so these events always follow the page_view.
 */
export function useTrackOnce(key: string | null, build: () => TagEvent | null): void {
  const last = useRef<string | null>(null)
  useEffect(() => {
    if (key === null || last.current === key) return
    last.current = key
    const event = build()
    if (event) track(event)
  })
}
