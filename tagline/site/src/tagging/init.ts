import { pushConsentDefault } from './consent'
import { gtag, observeOutsidePushes } from './dataLayer'
import { loadGa4 } from './ga4'
import { pushUserId } from './identity'

/**
 * Runs once, before React renders, in this order:
 *   1. dataLayer exists, pushes from outside the site are observed, and the global
 *      gtag() shim is defined
 *   2. consent default (all denied), then any stored choice as an update
 *   3. gtag.js + config, only if a GA4 measurement id is configured
 *   4. user_id, if a session survives from an earlier page load
 * so the first page_view already has consent state and identity in front of it.
 */
export function initTagging(opts: { userId: string | null }): void {
  observeOutsidePushes()
  globalThis.gtag = gtag
  pushConsentDefault()
  loadGa4()
  if (opts.userId) pushUserId(opts.userId)
}
