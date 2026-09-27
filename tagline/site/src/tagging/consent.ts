/**
 * Consent Mode v2 through the gtag() shim.
 *
 * `default` (all denied) is the first thing pushed on every page load. A stored choice
 * from an earlier visit is re-applied with `update` straight after it; otherwise the
 * banner asks. Events reach the dataLayer whatever the choice: Consent Mode tells the
 * Google tags how to behave, it does not stop the site from describing what happened.
 */
import { localStore, readJSON, writeJSON } from '../lib/storage'
import { gtag, type ConsentParams, type ConsentState } from './dataLayer'

export type ConsentChoice = 'accepted' | 'rejected'

const KEY = 'tagline.consent'

export const CONSENT_DEFAULTS = {
  analytics_storage: 'denied',
  ad_storage: 'denied',
  ad_user_data: 'denied',
  ad_personalization: 'denied',
} as const satisfies ConsentParams

export function consentParams(choice: ConsentChoice): Required<ConsentParams> {
  const v: ConsentState = choice === 'accepted' ? 'granted' : 'denied'
  return { analytics_storage: v, ad_storage: v, ad_user_data: v, ad_personalization: v }
}

export function storedConsent(): ConsentChoice | null {
  const stored = readJSON<unknown>(localStore(), KEY, null)
  return stored === 'accepted' || stored === 'rejected' ? stored : null
}

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())
export function subscribeConsent(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

let settingsOpen = false

/** The banner shows until a choice is stored, and again when the footer link reopens it. */
export const consentBannerVisible = (): boolean => settingsOpen || storedConsent() === null

export function openConsentSettings(): void {
  settingsOpen = true
  notify()
}

export function pushConsentDefault(): void {
  gtag('consent', 'default', { ...CONSENT_DEFAULTS })
  const stored = storedConsent()
  if (stored) gtag('consent', 'update', consentParams(stored))
}

export function setConsent(choice: ConsentChoice): void {
  writeJSON(localStore(), KEY, choice)
  settingsOpen = false
  gtag('consent', 'update', consentParams(choice))
  notify()
}
