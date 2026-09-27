/**
 * Fake sign-in. There is no password: the email only looks up (or opens) an account in
 * the stand-in directory, whose opaque id is what gets stored and tagged.
 */
import { localStore, readJSON, writeJSON } from '../lib/storage'
import { clearUserId, pushUserId } from '../tagging/identity'
import { accountIdFor } from './accounts'
import { track } from '../tagging/track'
import { login, signUp } from '../tagging/events'

export interface Session {
  user_id: string
}

const KEY = 'tagline.session'
const METHOD = 'email'

let current: Session | null | undefined
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

/** The only shape accounts.ts issues. A stored value that doesn't match is ignored, not pushed. */
const USER_ID = /^[0-9a-f]{32}$/

function readStored(): Session | null {
  const stored = readJSON<unknown>(localStore(), KEY, null)
  const id = typeof stored === 'object' && stored !== null ? (stored as Partial<Session>).user_id : undefined
  return typeof id === 'string' && USER_ID.test(id) ? { user_id: id } : null
}

export function getSession(): Session | null {
  if (current === undefined) current = readStored()
  return current
}

// Signing in or out in another tab changes this tab's user too: follow it, so events
// pushed here afterwards carry the right user_id. No login event: nothing was
// submitted in this tab, as on app start while signed in.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    // Not read yet: the first getSession() will read the new value anyway.
    if ((e.key !== KEY && e.key !== null) || current === undefined) return
    const next = readStored()
    if (next?.user_id === current?.user_id) return
    current = next
    if (next) pushUserId(next.user_id)
    else clearUserId()
    notify()
  })
}

export function subscribeSession(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

/**
 * Pushes `{ user_id }` first, then `login` or `sign_up`, so the event already
 * carries the id in GTM's data model.
 */
export async function signIn(email: string, mode: 'login' | 'sign_up'): Promise<Session> {
  const user_id = await accountIdFor(email)
  current = { user_id }
  writeJSON(localStore(), KEY, current)
  pushUserId(user_id)
  track(mode === 'login' ? login(METHOD) : signUp(METHOD))
  notify()
  return current
}

export function signOut(): void {
  current = null
  localStore().removeItem(KEY)
  clearUserId()
  notify()
}
