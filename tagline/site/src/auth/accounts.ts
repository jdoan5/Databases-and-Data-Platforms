/**
 * Stand-in for the account service a real store has. It gives each account an opaque
 * user_id: 128 random bits, not derived from the email, so whoever holds the ids (a GA4
 * property, the BigQuery export Stage 2 builds) cannot work out whose they are. That is
 * Google's User-ID rule: no information a third party could use to determine identity.
 *
 * The directory lives in this browser's localStorage, keyed by a SHA-256 of the email so
 * the email itself isn't kept. The key is only a lookup and never leaves the browser; the
 * id that gets tagged has no relation to it. A real backend would return the same id on
 * every device; this one is per browser.
 */
import { localStore, readJSON, writeJSON } from '../lib/storage'

const KEY = 'tagline.accounts'
const LOOKUP_PREFIX = 'tagline-supply/account-lookup/v1:'
const USER_ID = /^[0-9a-f]{32}$/

const toHex = (bytes: Uint8Array): string => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')

function normaliseEmail(email: string): string {
  return email.trim().toLowerCase()
}

async function lookupKey(email: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(LOOKUP_PREFIX + normaliseEmail(email)))
  return toHex(new Uint8Array(digest))
}

function readDirectory(): Record<string, unknown> {
  const stored = readJSON<unknown>(localStore(), KEY, {})
  return typeof stored === 'object' && stored !== null && !Array.isArray(stored) ? (stored as Record<string, unknown>) : {}
}

/** This email's account id, issued the first time it signs in or up in this browser. */
export async function accountIdFor(email: string): Promise<string> {
  const key = await lookupKey(email)
  const directory = readDirectory()
  const existing = directory[key]
  if (typeof existing === 'string' && USER_ID.test(existing)) return existing
  const id = toHex(crypto.getRandomValues(new Uint8Array(16)))
  writeJSON(localStore(), KEY, { ...directory, [key]: id })
  return id
}
