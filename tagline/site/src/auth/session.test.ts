import { beforeEach, describe, expect, it } from 'vitest'
import schema from '../../../tagging/events.schema.json'
import { createContract, toPayload, type JsonSchema } from '../tagging/contract'
import { pseudonymousUserId } from '../tagging/identity'
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
  })

  it('derives a stable id: same email in any case or padding, same id; different email, different id', async () => {
    const a = await pseudonymousUserId(EMAIL)
    expect(await pseudonymousUserId(`  ${EMAIL.toUpperCase()} `)).toBe(a)
    expect(await pseudonymousUserId('someone.else@example.com')).not.toBe(a)
  })

  it('clears user_id with null on sign-out', async () => {
    await signIn(EMAIL, 'login')
    signOut()
    expect(globalThis.dataLayer?.at(-1)).toEqual({ user_id: null })
    expect(getSession()).toBeNull()
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
