import { sessionStore } from '../lib/storage'

const KEY = 'tagline.debug'

/**
 * `?debug=1` turns the Tag Inspector on for the rest of the browser session (it is
 * remembered in sessionStorage, so it survives navigation and reloads); `?debug=0`
 * turns it off again.
 */
export function readDebugFlag(search: string): boolean {
  const flag = new URLSearchParams(search).get('debug')
  const store = sessionStore()
  try {
    if (flag === '1') store.setItem(KEY, '1')
    else if (flag === '0') store.removeItem(KEY)
  } catch {
    return flag === '1'
  }
  return store.getItem(KEY) === '1'
}
