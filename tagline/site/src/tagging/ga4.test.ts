import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import schema from '../../../tagging/events.schema.json'
import { findProduct, type Product } from '../catalog/catalog'
import { createContract, toPayload, type JsonSchema } from './contract'

const contract = createContract(schema as JsonSchema)
const mug = findProduct('TL-DRK-001') as Product

/** ga4.ts reads the measurement id at import time, so each case imports fresh modules. */
async function load(measurementId: string) {
  vi.resetModules()
  vi.stubEnv('VITE_GA4_MEASUREMENT_ID', measurementId)
  const ga4 = await import('./ga4')
  const { track } = await import('./track')
  const build = await import('./events')
  const identity = await import('./identity')
  return { ga4, track, build, identity }
}

const gtagCalls = () => (globalThis.dataLayer ?? []).map(toPayload).filter(Array.isArray)

describe('GA4 forwarding', () => {
  beforeEach(() => {
    globalThis.dataLayer = []
  })
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('is off by default: events stay in the dataLayer and no gtag command is pushed', async () => {
    const { ga4, track, build } = await load('')
    expect(ga4.ga4MeasurementId).toBeNull()
    ga4.loadGa4()
    track(build.addToCart(mug, 1))
    expect(gtagCalls()).toEqual([])
  })

  it('ignores a malformed id rather than loading a script with it', async () => {
    const { ga4 } = await load('G-123"><script>')
    expect(ga4.ga4MeasurementId).toBeNull()
  })

  it('when configured: config without an automatic page_view, then each event re-sent with ecommerce flattened', async () => {
    const { ga4, track, build } = await load('G-TEST1234')
    ga4.loadGa4()
    track(build.addToCart(mug, 2))
    track(build.search('mug'))
    const calls = gtagCalls()
    expect(calls[0][0]).toBe('js')
    expect(calls[1]).toEqual(['config', 'G-TEST1234', { send_page_view: false }])
    expect(calls[2]).toEqual([
      'event',
      'add_to_cart',
      { currency: 'USD', value: 27.98, items: [build.toItem(mug, 2)] },
    ])
    expect(calls[3]).toEqual(['event', 'search', { search_term: 'mug' }])
    for (const entry of globalThis.dataLayer ?? []) expect(contract.check(entry).errors).toEqual([])
  })

  it('sets user_id with set and clears it with set null', async () => {
    const { identity } = await load('G-TEST1234')
    identity.pushUserId('0123456789abcdef0123456789abcdef')
    identity.clearUserId()
    expect(gtagCalls()).toEqual([
      ['set', { user_id: '0123456789abcdef0123456789abcdef' }],
      ['set', { user_id: null }],
    ])
    for (const entry of globalThis.dataLayer ?? []) expect(contract.check(entry).errors).toEqual([])
  })
})
