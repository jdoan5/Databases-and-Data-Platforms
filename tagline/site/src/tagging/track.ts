import { pushToDataLayer } from './dataLayer'
import { forwardToGa4 } from './ga4'
import { isEcommerceEvent, type TagEvent } from './types'

/**
 * Push one event built by ./events.
 *
 * Ecommerce events are preceded by `{ ecommerce: null }`, as Google recommends, so
 * the GTM data model never merges a stale items array into the next event.
 *
 * The dataLayer gets a copy: once gtag.js or GTM has loaded, it writes
 * `gtm.uniqueEventId` into every object pushed, and that key must not ride along
 * into the GA4 params built from the original.
 */
export function track(event: TagEvent): void {
  if (isEcommerceEvent(event)) pushToDataLayer({ ecommerce: null })
  pushToDataLayer({ ...event })
  forwardToGa4(event)
}
