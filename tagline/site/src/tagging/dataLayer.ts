/**
 * window.dataLayer and the standard gtag() shim.
 *
 * Every push in the site goes through pushToDataLayer(), which validates it against
 * the contract, records it for the Tag Inspector, warns on failure, and then pushes it
 * unchanged. Invalid pushes are still pushed: dropping data silently would hide the
 * bug the validator exists to show.
 *
 * dataLayer.push itself is never patched. GTM and gtag.js both replace it with their
 * own function when they load, so a wrapper there would be lost.
 */
import { toPayload } from './contract'
import { recordPush } from './log'
import { validateEntry } from './validate'

declare global {
  // Declared on globalThis (not only window) so the unit tests can run in Node.
  var dataLayer: unknown[] | undefined
  var gtag: ((...args: GtagCommand) => void) | undefined
}

export type ConsentState = 'granted' | 'denied'
export type ConsentParams = Partial<
  Record<'analytics_storage' | 'ad_storage' | 'ad_user_data' | 'ad_personalization', ConsentState>
>

export type GtagCommand =
  | ['consent', 'default' | 'update', ConsentParams]
  | ['js', Date]
  | ['config', string, Record<string, unknown>]
  | ['set', Record<string, unknown>]
  | ['event', string, Record<string, unknown>]

export function getDataLayer(): unknown[] {
  globalThis.dataLayer ??= []
  return globalThis.dataLayer
}

export function pushToDataLayer(entry: object): void {
  const check = validateEntry(entry)
  if (!check.valid) {
    console.warn(`[tagline] dataLayer push "${check.label}" does not match the contract:`, check.errors, entry)
  }
  recordPush(toPayload(entry), check)
  getDataLayer().push(entry)
}

/**
 * The standard shim: `function gtag(){dataLayer.push(arguments);}`.
 * It must push the `arguments` object itself; gtag.js ignores plain arrays.
 */
export function gtag(..._command: GtagCommand): void {
  // oxlint-disable-next-line prefer-rest-params
  pushToDataLayer(arguments)
}
