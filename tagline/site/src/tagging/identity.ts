/**
 * user_id: the key Stage 2 uses to stitch anonymous sessions to a signed-in user.
 *
 * GA4 policy forbids sending PII, so the email never enters the dataLayer. The id is
 * SHA-256 over a site-specific prefix plus the normalised email, truncated to 32 hex
 * characters. That makes it stable (same email, same id, any device) and not the email.
 *
 * It is pseudonymous, not anonymous: anyone who has this code and a candidate email
 * can recompute the id and confirm a match. In production the id would be the account
 * key issued by the auth backend; this project has no backend, so a hash stands in.
 */
import { pushToDataLayer } from './dataLayer'
import { setGa4UserId } from './ga4'

const ID_PREFIX = 'tagline-supply/user-id/v1:'

export function normaliseEmail(email: string): string {
  return email.trim().toLowerCase()
}

export async function pseudonymousUserId(email: string): Promise<string> {
  const bytes = new TextEncoder().encode(ID_PREFIX + normaliseEmail(email))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
  return hex.slice(0, 32)
}

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
