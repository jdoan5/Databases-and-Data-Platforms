/**
 * window.dataLayer and the standard gtag() shim.
 *
 * Every push the site makes goes through pushToDataLayer(), which validates it against
 * the contract, records it for the Tag Inspector, warns on failure, and then pushes it
 * unchanged. Invalid pushes are still pushed: dropping data silently would hide the
 * bug the validator exists to show.
 *
 * Pushes made some other way (typed into the console, or by another script) are
 * checked too: observeOutsidePushes() wraps dataLayer.push once at startup and reports
 * anything that did not come through pushToDataLayer(). GTM and gtag.js wrap push
 * again when they load; the site's own pushes do not depend on that wrapper, because
 * they are validated before push is called.
 */
import { toPayload, type Check } from './contract'
import { recordPush, type PushSource } from './log'
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

function report(entry: unknown, source: PushSource): Check {
  const check = validateEntry(entry)
  if (!check.valid) {
    const from = source === 'outside' ? ' (not pushed by the site)' : ''
    console.warn(`[tagline] dataLayer push "${check.label}"${from} does not match the contract:`, check.errors, entry)
  }
  recordPush(toPayload(entry), check, source)
  return check
}

/** True while pushToDataLayer() is pushing, so the outside-push wrapper skips it. */
let pushingOwn = false

export function pushToDataLayer(entry: object): void {
  report(entry, 'site')
  pushingOwn = true
  try {
    getDataLayer().push(entry)
  } finally {
    pushingOwn = false
  }
}

const OBSERVED = Symbol.for('tagline.observed')

/**
 * Validates and records pushes that bypass pushToDataLayer(), such as
 * `dataLayer.push({ event: 'add_to_cart' })` typed into the console, so the Tag
 * Inspector shows them with ✓ or ✗ like the site's own. Installed once, before
 * anything else is pushed. The wrapper calls the push it replaced, so GTM or gtag.js
 * wrapping it later keeps working.
 */
export function observeOutsidePushes(): void {
  const dl = getDataLayer() as unknown[] & { [OBSERVED]?: true }
  if (dl[OBSERVED]) return
  const previous = dl.push
  dl.push = function (this: unknown[], ...entries: unknown[]): number {
    if (!pushingOwn) for (const entry of entries) report(entry, 'outside')
    return previous.apply(this, entries)
  }
  dl[OBSERVED] = true
}

/**
 * The standard shim: `function gtag(){dataLayer.push(arguments);}`.
 * It must push the `arguments` object itself; gtag.js ignores plain arrays.
 */
export function gtag(..._command: GtagCommand): void {
  pushToDataLayer(arguments)
}
