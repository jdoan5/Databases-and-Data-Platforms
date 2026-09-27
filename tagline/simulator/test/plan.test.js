import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CAMPAIGNS, CHANNELS, PRODUCTS, buildPlan, identity, planStats, searchProducts } from '../src/plan.js'

test('same seed, same plan', () => {
  assert.deepEqual(buildPlan({ seed: 'abc', people: 30 }), buildPlan({ seed: 'abc', people: 30 }))
  assert.deepEqual(buildPlan(), buildPlan())
})

test('another seed changes the journeys but not who the people are', () => {
  const a = buildPlan({ seed: 'one', people: 20 })
  const b = buildPlan({ seed: 'two', people: 20 })
  assert.notDeepEqual(a.people.map((p) => p.devices), b.people.map((p) => p.devices))
  assert.deepEqual(
    a.people.map(({ personId, email, accountId }) => ({ personId, email, accountId })),
    b.people.map(({ personId, email, accountId }) => ({ personId, email, accountId })),
  )
})

test('another population gives other account ids', () => {
  assert.notEqual(identity(1, 'tagline').accountId, identity(1, 'other').accountId)
})

test('each person has a stream of their own: a smaller plan is a prefix of a larger one', () => {
  assert.deepEqual(buildPlan({ people: 10 }).people, buildPlan({ people: 25 }).people.slice(0, 10))
})

test('identities: opaque 32-hex account ids, unique, emails on a reserved domain', () => {
  const plan = buildPlan({ people: 200 })
  const ids = plan.people.map((p) => p.accountId)
  assert.ok(ids.every((id) => /^[0-9a-f]{32}$/.test(id)))
  assert.equal(new Set(ids).size, ids.length)
  assert.ok(plan.people.every((p) => p.email.endsWith('@tagline-sim.example')))
  assert.ok(plan.people.every((p) => !p.accountId.includes(p.email)))
})

test('the default plan covers every channel, consent choice, sign-in kind and funnel depth', () => {
  const s = planStats(buildPlan())
  for (const c of CHANNELS) assert.ok(s.byChannel[c] > 0, `channel ${c}`)
  for (const c of ['accept', 'reject', 'ignore']) assert.ok(s.byConsent[c] > 0, `consent ${c}`)
  for (const k of ['sign_up', 'login']) assert.ok(s.bySignIn[k] > 0, `sign-in ${k}`)
  for (const d of ['bounce', 'browse', 'cart_abandon', 'checkout_abandon', 'purchase']) assert.ok(s.byDepth[d] > 0, `depth ${d}`)
  assert.ok(s.crossDevicePeople > 0)
})

test('campaign landings carry utm_source / medium / campaign; organic has a Google referrer; direct neither', () => {
  for (const d of buildPlan({ people: 100 }).people.flatMap((p) => p.devices)) {
    const url = new URL(d.landing, 'http://localhost')
    const c = CAMPAIGNS[d.channel]
    if (c) {
      assert.equal(url.searchParams.get('utm_source'), c.source)
      assert.equal(url.searchParams.get('utm_medium'), c.medium)
      assert.equal(url.searchParams.get('utm_campaign'), c.campaign)
      assert.equal(d.referrer, undefined)
    } else {
      assert.equal(url.search, '')
      assert.equal(d.referrer, d.channel === 'organic' ? 'https://www.google.com/' : undefined)
    }
    assert.equal(d.steps[0].do, 'land')
    assert.equal(d.steps[0].url, d.landing)
  }
})

test('sign-ins: sign_up only on a first device, logins on later ones belong to people who signed up or have an account', () => {
  for (const p of buildPlan({ people: 200 }).people) {
    const [first, ...later] = p.devices
    for (const d of later) assert.notEqual(d.signIn, 'sign_up')
    if (later.some((d) => d.signIn === 'login')) assert.equal(first.signIn, 'sign_up')
    for (const d of p.devices) {
      const steps = d.steps.filter((s) => s.do === 'sign_in')
      assert.equal(steps.length, d.signIn ? 1 : 0)
      if (d.signIn) assert.equal(steps[0].mode, d.signIn)
      if (d.signIn) assert.equal(steps[0].email, p.email)
    }
  }
})

test('every journey is one the site can perform, in order', () => {
  const inCategory = (slug) => PRODUCTS.filter((p) => p.item_category.toLowerCase() === slug).map((p) => p.item_id)
  for (const d of buildPlan({ people: 200 }).people.flatMap((p) => p.devices)) {
    let list = null
    let product = null
    const land = new URL(d.landing, 'http://localhost').pathname
    if (land === '/') list = PRODUCTS.map((p) => p.item_id)
    else if (land.startsWith('/category/')) list = inCategory(land.split('/')[2])
    else product = land.split('/')[2]
    let cartUnits = 0
    const order = []
    for (const s of d.steps) {
      order.push(s.do)
      assert.ok(Number.isInteger(s.thinkMs) && s.thinkMs > 0)
      if (s.do === 'category') [list, product] = [inCategory(s.slug), null]
      if (s.do === 'search') [list, product] = [s.term.includes('@') ? [] : searchProducts(s.term).map((p) => p.item_id), null]
      if (s.do === 'sign_in') [list, product] = [PRODUCTS.map((p) => p.item_id), null]
      if (s.do === 'product') {
        assert.ok(list?.includes(s.itemId), `${d.deviceId}: ${s.itemId} is not on the page it is clicked from`)
        ;[list, product] = [null, s.itemId]
      }
      if (s.do === 'add') {
        assert.equal(s.itemId, product, `${d.deviceId}: add happens on that product's page`)
        cartUnits += s.quantity
      }
      if (s.do === 'cart_less') cartUnits -= 1
      if (s.do === 'cart' || s.do === 'checkout') [list, product] = [null, null]
      if (s.do === 'checkout') assert.ok(cartUnits > 0, `${d.deviceId}: checkout with an empty cart`)
    }
    if (d.depth === 'purchase') {
      const idx = ['cart', 'checkout', 'shipping', 'payment', 'place_order'].map((x) => order.indexOf(x))
      assert.ok(idx.every((i, k) => i >= 0 && (k === 0 || i > idx[k - 1])), `${d.deviceId}: ${order.join(' ')}`)
    } else assert.ok(!order.includes('place_order'))
  }
})

test('the PII probes are planned: an email typed into search, an email in a newsletter link', () => {
  const devices = buildPlan().people.flatMap((p) => p.devices)
  assert.ok(devices.some((d) => d.probes?.includes('email_in_search') && d.steps.some((s) => s.do === 'search' && s.term.includes('@'))))
  assert.ok(devices.some((d) => d.probes?.includes('email_in_landing_url') && d.landing.includes('subscriber=')))
})

test('rejects bad options', () => {
  assert.throws(() => buildPlan({ people: 0 }))
  assert.throws(() => buildPlan({ population: 'Has Spaces' }))
})
