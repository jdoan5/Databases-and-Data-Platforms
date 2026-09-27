/**
 * user_id: the key Stage 2 uses to stitch anonymous sessions to a signed-in user.
 *
 * The id is the account's opaque key (auth/accounts.ts), never the email or anything
 * derived from it, so GA4's PII policy and User-ID rule both hold.
 */
import { pushToDataLayer } from './dataLayer'
import { setGa4UserId } from './ga4'

/** `{ user_id }` goes in before any event that should carry it. */
export function pushUserId(userId: string): void {
  pushToDataLayer({ user_id: userId })
  setGa4UserId(userId)
}

/** Signing out sets user_id to null (Google: never "" or the string "null"). */
export function clearUserId(): void {
  pushToDataLayer({ user_id: null })
  setGa4UserId(null)
}
