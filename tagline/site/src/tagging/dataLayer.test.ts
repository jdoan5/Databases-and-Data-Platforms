import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { observeOutsidePushes, pushToDataLayer } from './dataLayer'
import { clearLog, getLog } from './log'

const summary = () => getLog().map((e) => [e.label, e.source, e.valid])

describe('pushes that bypass the site', () => {
  beforeEach(() => {
    globalThis.dataLayer = []
    clearLog()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('validates and records a push typed into the console, and the site’s own pushes once each', () => {
    observeOutsidePushes()
    observeOutsidePushes() // a second call must not wrap twice
    globalThis.dataLayer?.push({ event: 'add_to_cart' })
    pushToDataLayer({ ecommerce: null })

    // Spread: the wrapped array carries its own push property, which toEqual would compare.
    expect([...(globalThis.dataLayer ?? [])]).toEqual([{ event: 'add_to_cart' }, { ecommerce: null }])
    expect(summary()).toEqual([
      ['add_to_cart', 'outside', false],
      ['ecommerce: null', 'site', true],
    ])
    expect(getLog()[0].errors).toEqual(['(root): missing "ecommerce"'])
    expect(console.warn).toHaveBeenCalledTimes(1)
  })

  it('keeps working when another library wraps dataLayer.push afterwards, as GTM and gtag.js do', () => {
    observeOutsidePushes()
    const dl = globalThis.dataLayer as unknown[]
    const seenByLibrary: unknown[] = []
    const previous = dl.push
    dl.push = function (...entries: unknown[]) {
      const n = previous.apply(dl, entries)
      seenByLibrary.push(...entries)
      return n
    }

    pushToDataLayer({ ecommerce: null })
    dl.push({ event: 'gtm.dom' })

    expect(seenByLibrary).toEqual([{ ecommerce: null }, { event: 'gtm.dom' }])
    expect(summary()).toEqual([
      ['ecommerce: null', 'site', true],
      ['gtm.dom', 'outside', true],
    ])
  })
})
