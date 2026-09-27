/**
 * Fake sign-in. There is no backend and no password: the email is hashed into a
 * pseudonymous user_id and then discarded. Only the id is stored or tagged.
 */
import { localStore, readJSON, writeJSON } from '../lib/storage'
import { clearUserId, pseudonymousUserId, pushUserId } from '../tagging/identity'
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

export function getSession(): Session | null {
  if (current === undefined) {
    const stored = readJSON<unknown>(localStore(), KEY, null)
    current =
      typeof stored === 'object' && stored !== null && typeof (stored as Session).user_id === 'string'
        ? { user_id: (stored as Session).user_id }
        : null
  }
  return current
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
  const user_id = await pseudonymousUserId(email)
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
