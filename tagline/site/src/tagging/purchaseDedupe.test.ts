import { describe, expect, it } from 'vitest'
import { memoryStore } from '../lib/storage'
import { claimTransaction } from './purchaseDedupe'

describe('purchase dedupe', () => {
  it('claims a transaction id once; every later attempt is refused', () => {
    const store = memoryStore()
    expect(claimTransaction('TL-A-1', store)).toBe(true)
    expect(claimTransaction('TL-A-1', store)).toBe(false) // reload
    expect(claimTransaction('TL-A-1', store)).toBe(false) // back button, second tab
    expect(claimTransaction('TL-A-2', store)).toBe(true) // a different order still fires
  })

  it('survives a reload because the claim is in storage, not in memory', () => {
    const store = memoryStore()
    claimTransaction('TL-B-1', store)
    const raw = store.getItem('tagline.purchases_sent')
    const afterReload = memoryStore()
    afterReload.setItem('tagline.purchases_sent', raw ?? '')
    expect(claimTransaction('TL-B-1', afterReload)).toBe(false)
  })

  it('treats corrupt storage as empty instead of throwing', () => {
    const store = memoryStore()
    store.setItem('tagline.purchases_sent', '{not json')
    expect(claimTransaction('TL-C-1', store)).toBe(true)
    store.setItem('tagline.purchases_sent', '{"an":"object"}')
    expect(claimTransaction('TL-C-2', store)).toBe(true)
  })

  it('keeps the last 100 ids', () => {
    const store = memoryStore()
    for (let i = 0; i < 150; i++) claimTransaction(`TL-D-${i}`, store)
    expect(JSON.parse(store.getItem('tagline.purchases_sent') ?? '[]')).toHaveLength(100)
    expect(claimTransaction('TL-D-149', store)).toBe(false)
  })
})
