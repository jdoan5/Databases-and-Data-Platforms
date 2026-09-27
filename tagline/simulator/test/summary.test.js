import assert from 'node:assert/strict'
import { test } from 'node:test'
import { channelOf, summarise } from '../src/summary.js'
import { parseCollect } from '../src/hits.js'

const ACCOUNT = '0123456789abcdef0123456789abcdef'
const LANDING = '/?utm_source=newsletter&utm_medium=email&utm_campaign=newsletter_oct'

/** One person, one device: newsletter landing, accept, sign up. */
const plan = {
  seed: 't',
  population: 't',
  people: [
    {
      personId: 'p001',
      email: 't-shopper-001@tagline-sim.example',
      accountId: ACCOUNT,
      devices: [{ deviceId: 'p001-d1', channel: 'newsletter_oct', consent: 'accept', signIn: 'sign_up', depth: 'browse', landing: LANDING, steps: [] }],
    },
  ],
}
const device = (over = {}) => ({
  deviceId: 'p001-d1',
  personId: 'p001',
  userId: ACCOUNT,
  transactionIds: [],
  dataLayerEvents: ['page_view', 'view_item_list', 'sign_up'],
  errors: [],
  ...over,
})

const q = (params) => `https://www.google-analytics.com/g/collect?v=2&tid=G-DRYRUN0000&cid=1.2&sid=3&${new URLSearchParams(params)}`
const dl = `http://localhost:5190${LANDING}`
function hitsFrom(requests) {
  return requests.flatMap((url, request) => parseCollect(url, '').map((h, line) => ({ ...h, i: request, request, line, device_id: 'p001-d1', person_id: 'p001' })))
}
const requestsFrom = (urls) => urls.map((url, i) => ({ i, device_id: 'p001-d1', collect: true, host: 'www.google-analytics.com', path: '/g/collect', method: 'POST', resource_type: 'fetch', pii: [], url }))

const good = [
  q({ en: 'page_view', gcs: 'G100', dl, _ss: '1' }),
  q({ en: 'view_item_list', gcs: 'G100', dl }),
  q({ en: 'user_engagement', gcs: 'G111', dl }),
  q({ en: 'sign_up', gcs: 'G111', dl: 'http://localhost:5190/signin', uid: ACCOUNT }),
]

const run = (urls, dev = device(), extra = {}) =>
  summarise({ plan, devices: [dev], hits: hitsFrom(urls), requests: requestsFrom(urls), mode: 'dry-run', ...extra })

test('a clean device passes every check', () => {
  const s = run(good)
  assert.deepEqual(s.failures, [])
  assert.equal(s.channels.newsletter_oct.firstHit, 1)
  assert.equal(s.byEvent.page_view, 1)
  assert.equal(s.uid.hitsWithUid, 1)
})

test('the first hit losing its UTM is an attribution failure', () => {
  const urls = [q({ en: 'page_view', gcs: 'G100', dl: 'http://localhost:5190/' }), ...good.slice(1)]
  assert.ok(run(urls).failures.some((f) => f.includes('first hit reads as direct')))
})

test('a uid before the sign-up, or a different uid, fails', () => {
  const early = [q({ en: 'page_view', gcs: 'G100', dl, uid: ACCOUNT }), ...good.slice(1)]
  assert.ok(run(early).failures.some((f) => f.includes('before the sign_up')))
  const other = [...good.slice(0, 3), q({ en: 'sign_up', gcs: 'G111', dl, uid: 'f'.repeat(32) })]
  assert.ok(run(other).failures.some((f) => f.includes("not the person's account id")))
})

test('an event pushed but never sent fails the forwarding check', () => {
  const s = run(good, device({ dataLayerEvents: ['page_view', 'view_item_list', 'sign_up', 'search'] }))
  assert.ok(s.failures.some((f) => f.includes('search pushed 1× to the dataLayer, sent 0×')))
})

test('personal data and dry-run leaks fail', () => {
  const requests = requestsFrom(good)
  requests[1].pii = ['email-shaped text: jo@example.com']
  const s = summarise({ plan, devices: [device()], hits: hitsFrom(good), requests, leaks: [{ url: 'https://www.google-analytics.com/g/collect' }], mode: 'dry-run' })
  assert.ok(s.failures.some((f) => f.startsWith('personal data')))
  assert.ok(s.failures.some((f) => f.includes('completed instead of being aborted')))
})

test('a rejected device with a granted hit fails', () => {
  const rejected = structuredClone(plan)
  rejected.people[0].devices[0].consent = 'reject'
  const s = summarise({ plan: rejected, devices: [device()], hits: hitsFrom(good), requests: requestsFrom(good), mode: 'dry-run' })
  assert.ok(s.failures.some((f) => f.includes('consent reject')))
})

test('channelOf reads a hit the way GA4 reads a session’s first event', () => {
  assert.equal(channelOf({ campaign: { campaign: 'fall_launch' }, dl: 'http://localhost/' }), 'fall_launch')
  assert.equal(channelOf({ campaign: null, dl: 'http://localhost/', dr: 'https://www.google.com/' }), 'organic')
  assert.equal(channelOf({ campaign: null, dl: 'http://localhost/cart', dr: 'http://localhost/' }), 'direct')
  assert.equal(channelOf({ campaign: null, dl: 'http://localhost/', dr: 'https://news.example/' }), 'referral:news.example')
})
