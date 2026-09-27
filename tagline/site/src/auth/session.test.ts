import { beforeEach, describe, expect, it, vi } from 'vitest'
import schema from '../../../tagging/events.schema.json'
import { createContract, toPayload, type JsonSchema } from '../tagging/contract'
import { getSession, signIn, signOut } from './session'

const contract = createContract(schema as JsonSchema)
const EMAIL = 'Jo.Doan+Shop@Example.com'

/** Everything in the dataLayer as one string, in every casing and encoding we can think of. */
function dataLayerText(): string {
  const text = JSON.stringify((globalThis.dataLayer ?? []).map(toPayload))
  return text.toLowerCase()
}

describe('fake sign-in', () => {
  beforeEach(() => {
    globalThis.dataLayer = []
    signOut()
    globalThis.dataLayer = []
  })

  it('pushes { user_id } then login, and nothing else', async () => {
    const session = await signIn(EMAIL, 'login')
    expect(globalThis.dataLayer).toEqual([{ user_id: session.user_id }, { event: 'login', method: 'email' }])
    expect(session.user_id).toMatch(/^[0-9a-f]{32}$/)
  })

  it('pushes sign_up (and not login) for a new account', async () => {
    await signIn(EMAIL, 'sign_up')
    expect((globalThis.dataLayer ?? []).map((e) => (e as { event?: string }).event).filter(Boolean)).toEqual(['sign_up'])
  })

  it('never puts the email, or any part of it, in a push', async () => {
    await signIn(EMAIL, 'login')
    signOut()
    await signIn(EMAIL, 'sign_up')
    const text = dataLayerText()
    for (const fragment of [EMAIL, EMAIL.toLowerCase(), encodeURIComponent(EMAIL), 'jo.doan', 'example.com', '@', '%40']) {
      expect(text).not.toContain(fragment.toLowerCase())
    }
  })

  it('does not keep the email in storage either', async () => {
    await signIn(EMAIL, 'login')
    expect(JSON.stringify(getSession()).toLowerCase()).not.toContain('example.com')
    const storage = await import('../lib/storage')
    expect((storage.localStore().getItem('tagline.accounts') ?? '').toLowerCase()).not.toContain('example.com')
  })

  it('keeps one account id per email in this browser, in any case or padding; another email gets another id', async () => {
    const a = (await signIn(EMAIL, 'sign_up')).user_id
    signOut()
    expect((await signIn(`  ${EMAIL.toUpperCase()} `, 'login')).user_id).toBe(a)
    signOut()
    expect((await signIn('someone.else@example.com', 'login')).user_id).not.toBe(a)
  })

  it('issues a random id, not one computed from the email (Google User-ID rule)', async () => {
    const first = (await signIn(EMAIL, 'sign_up')).user_id
    signOut()
    const storage = await import('../lib/storage')
    storage.localStore().removeItem('tagline.accounts') // a fresh browser: nothing to look up
    expect((await signIn(EMAIL, 'sign_up')).user_id).not.toBe(first)
  })

  it('clears user_id with null on sign-out', async () => {
    await signIn(EMAIL, 'login')
    signOut()
    expect(globalThis.dataLayer?.at(-1)).toEqual({ user_id: null })
    expect(getSession()).toBeNull()
  })

  it('ignores a stored session whose id is not 32 hex characters, rather than pushing it on app start', async () => {
    vi.resetModules()
    const storage = await import('../lib/storage')
    storage.localStore().setItem('tagline.session', JSON.stringify({ user_id: 'jo.doan@example.com' }))
    const fresh = await import('./session')
    expect(fresh.getSession()).toBeNull()
  })

  it('follows a sign-in or sign-out in another tab: user_id pushed or cleared, and no login event', async () => {
    vi.resetModules()
    const onStorage: ((e: { key: string | null }) => void)[] = []
    vi.stubGlobal('window', { addEventListener: (type: string, l: (typeof onStorage)[number]) => type === 'storage' && onStorage.push(l) })
    const storage = await import('../lib/storage')
    const fresh = await import('./session')
    vi.unstubAllGlobals()
    const otherTab = (value: string | null) => {
      if (value === null) storage.localStore().removeItem('tagline.session')
      else storage.localStore().setItem('tagline.session', value)
      onStorage.forEach((l) => l({ key: 'tagline.session' }))
    }
    const id = '0123456789abcdef0123456789abcdef'
    expect(fresh.getSession()).toBeNull() // app start, signed out
    globalThis.dataLayer = []
    otherTab(JSON.stringify({ user_id: id }))
    expect(fresh.getSession()).toEqual({ user_id: id })
    otherTab(JSON.stringify({ user_id: id })) // same user: nothing new to push
    otherTab(null)
    expect(fresh.getSession()).toBeNull()
    expect(globalThis.dataLayer).toEqual([{ user_id: id }, { user_id: null }])
  })

  it('pushes only entries the contract accepts', async () => {
    await signIn(EMAIL, 'sign_up')
    signOut()
    await signIn(EMAIL, 'login')
    for (const entry of globalThis.dataLayer ?? []) {
      const result = contract.check(entry)
      expect(result.errors).toEqual([])
    }
  })
})
