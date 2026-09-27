/**
 * Optional forwarding to a GA4 property.
 *
 * Off unless VITE_GA4_MEASUREMENT_ID is set at build time (no id is committed). When
 * on: gtag.js loads, `config` runs with send_page_view: false (the site sends its own
 * page_view on every route change), and each event is re-sent as
 * gtag('event', name, params) with the ecommerce object flattened into the params,
 * which is the shape gtag.js expects.
 *
 * Consent Mode applies either way: gtag.js reads the consent default/update commands
 * from the dataLayer and adjusts what it stores and sends.
 */
import { gtag } from './dataLayer'
import type { TagEvent } from './types'

const raw = (import.meta.env.VITE_GA4_MEASUREMENT_ID ?? '').trim()

/** Only a well-formed GA4 id is accepted; anything else leaves forwarding off. */
export const ga4MeasurementId: string | null = /^G-[A-Z0-9]{4,}$/.test(raw) ? raw : null

let loaded = false

export function loadGa4(): void {
  if (!ga4MeasurementId || loaded) return
  loaded = true
  if (typeof document !== 'undefined') {
    const script = document.createElement('script')
    script.async = true
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ga4MeasurementId)}`
    document.head.append(script)
  }
  gtag('js', new Date())
  gtag('config', ga4MeasurementId, { send_page_view: false })
}

export function ga4Params(event: TagEvent): { name: string; params: Record<string, unknown> } {
  const { event: name, ...rest } = event
  if ('ecommerce' in rest) {
    const { ecommerce, ...others } = rest
    return { name, params: { ...others, ...ecommerce } }
  }
  return { name, params: rest }
}

export function forwardToGa4(event: TagEvent): void {
  if (!ga4MeasurementId) return
  const { name, params } = ga4Params(event)
  gtag('event', name, params)
}

/**
 * `set` once the tag has loaded, as the tagging plan specifies: the id on sign-in (and
 * on app start while signed in), null on sign-out. Google: never "" or "null".
 */
export function setGa4UserId(userId: string | null): void {
  if (!ga4MeasurementId) return
  gtag('set', { user_id: userId })
}
