/**
 * localStorage / sessionStorage with a fallback.
 *
 * Storage can be missing (Node, where the unit tests run) or throw (Safari private
 * mode, storage disabled). Either way the site keeps working for the length of the
 * page load by falling back to an in-memory map.
 */

export interface KeyValueStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

export function memoryStore(): KeyValueStore {
  const map = new Map<string, string>()
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  }
}

function usable(kind: 'localStorage' | 'sessionStorage'): KeyValueStore | null {
  try {
    const s = (globalThis as Partial<Record<typeof kind, Storage>>)[kind]
    if (!s) return null
    const probe = '__tagline_probe__'
    s.setItem(probe, '1')
    s.removeItem(probe)
    return s
  } catch {
    return null
  }
}

let local: KeyValueStore | undefined
let session: KeyValueStore | undefined

export const localStore = (): KeyValueStore => (local ??= usable('localStorage') ?? memoryStore())
export const sessionStore = (): KeyValueStore => (session ??= usable('sessionStorage') ?? memoryStore())

export function readJSON<T>(store: KeyValueStore, key: string, fallback: T): T {
  try {
    const raw = store.getItem(key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

export function writeJSON(store: KeyValueStore, key: string, value: unknown): void {
  try {
    store.setItem(key, JSON.stringify(value))
  } catch {
    // Quota or disabled storage: the in-page state still works, it just won't persist.
  }
}
