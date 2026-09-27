/**
 * Keeping email addresses out of free-text parameters.
 *
 * Google's policy forbids sending PII to GA4 in any parameter, and the three places
 * user-typed text can reach a tag here are the search term, the page title (the search
 * page shows the term) and URLs (the search page puts it in ?q=). The builders run
 * those values through this module, so the contract's email check should never fire;
 * if it does, something bypassed a builder.
 */

/** Same pattern as $defs/looks_like_email in events.schema.json: raw or URL-encoded @. */
export const LOOKS_LIKE_EMAIL = /[^\s@]+(@|%40)[^\s@]+\.[^\s@]+/i

const EMAIL_IN_TEXT = /[^\s@]+(?:@|%40)[^\s@]+\.[^\s@]+/gi

export const REDACTED = '[email]'

/** Replaces anything email-shaped in a piece of text. */
export function redactText(text: string): string {
  return text.replace(EMAIL_IN_TEXT, REDACTED)
}

/** GA4 caps event parameter values at 100 characters. */
export const MAX_PARAM_LENGTH = 100

/** trim, redact, collapse whitespace, cap at 100 characters. */
export function cleanSearchTerm(raw: string): string {
  return redactText(raw.trim()).replace(/\s+/g, ' ').slice(0, MAX_PARAM_LENGTH).trim()
}

const safeDecode = (s: string): string => {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

/**
 * A URL fit for page_location / page_referrer: http(s) only, no fragment (GA4 drops
 * it anyway), no credentials, and any query value or path segment that looks like an
 * email replaced. If the whole URL still matches the email pattern after that, only
 * the origin is kept. Returns undefined for anything that is not an http(s) URL.
 */
export function cleanUrl(raw: string, maxLength: number): string | undefined {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return undefined
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined
  url.hash = ''
  url.username = ''
  url.password = ''

  const pairs = [...url.searchParams]
  if (pairs.some(([k, v]) => LOOKS_LIKE_EMAIL.test(k) || LOOKS_LIKE_EMAIL.test(v))) {
    const params = new URLSearchParams()
    for (const [k, v] of pairs) params.append(LOOKS_LIKE_EMAIL.test(k) ? REDACTED : k, LOOKS_LIKE_EMAIL.test(v) ? REDACTED : v)
    url.search = params.toString()
  }

  const segments = url.pathname.split('/')
  if (segments.some((s) => LOOKS_LIKE_EMAIL.test(safeDecode(s)))) {
    url.pathname = segments.map((s) => (LOOKS_LIKE_EMAIL.test(safeDecode(s)) ? REDACTED : s)).join('/')
  }

  let out = url.toString()
  if (LOOKS_LIKE_EMAIL.test(out)) out = `${url.origin}/`
  return out.slice(0, maxLength)
}
