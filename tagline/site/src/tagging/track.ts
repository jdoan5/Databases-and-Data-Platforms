import { pushToDataLayer } from './dataLayer'
import { forwardToGa4 } from './ga4'
import { isEcommerceEvent, type TagEvent } from './types'

/**
 * Push one event built by ./events.
 *
 * Ecommerce events are preceded by `{ ecommerce: null }`, as Google recommends, so
 * the GTM data model never merges a stale items array into the next event.
 */
export function track(event: TagEvent): void {
  if (isEcommerceEvent(event)) pushToDataLayer({ ecommerce: null })
  pushToDataLayer(event)
  forwardToGa4(event)
}
