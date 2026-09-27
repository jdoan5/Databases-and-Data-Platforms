import assert from 'node:assert/strict'
import { test } from 'node:test'
import { analyticsConsent, campaignFromUrl, findPii, isCollect, isGtagScript, parseCollect, parseItem } from '../src/hits.js'
import { accountDirectory } from '../src/run.js'
import { createHash } from 'node:crypto'

const base =
  'https://www.google-analytics.com/g/collect?v=2&tid=G-DRYRUN0000&gcs=G111&cid=123.456&sid=789&sct=1&seg=1' +
  '&dl=http%3A%2F%2Flocalhost%3A5190%2Fproduct%2FTL-DRK-003%3Futm_source%3Dfacebook%26utm_medium%3Dpaid_social%26utm_campaign%3Dretarget_q4' +
  '&dt=Cold%20Brew%20Tumbler&_s=4'

test('a request without a body is one event, from the query string', () => {
  const [hit] = parseCollect(`${base}&en=page_view&_ss=1&_fv=1`, '')
  assert.equal(hit.en, 'page_view')
  assert.equal(hit.cid, '123.456')
  assert.equal(hit.session_start, true)
  assert.equal(hit.first_visit, true)
  assert.deepEqual(hit.campaign, { source: 'facebook', medium: 'paid_social', campaign: 'retarget_q4' })
})

test('a batched request is one event per body line, each line overriding the shared query', () => {
  const body = [
    'en=add_to_cart&cu=USD&pr1=idTL-DRK-003~nmCold%20Brew%20Tumbler~brTagline%20Supply~caDrinkware~pr24.5~qt2&epn.value=49&_et=198',
    'en=page_view&dl=http%3A%2F%2Flocalhost%3A5190%2Fcart&dr=http%3A%2F%2Flocalhost%3A5190%2Fproduct%2FTL-DRK-003',
    'en=login&ep.method=email&uid=0123456789abcdef0123456789abcdef',
  ].join('\r\n')
  const hits = parseCollect(base, body)
  assert.deepEqual(hits.map((h) => h.en), ['add_to_cart', 'page_view', 'login'])
  assert.deepEqual(hits[0].items, [{ item_id: 'TL-DRK-003', item_name: 'Cold Brew Tumbler', item_brand: 'Tagline Supply', item_category: 'Drinkware', price: 24.5, quantity: 2 }])
  assert.equal(hits[0].epn.value, 49)
  assert.equal(hits[0].campaign.campaign, 'retarget_q4')
  assert.equal(hits[1].dl, 'http://localhost:5190/cart')
  assert.equal(hits[1].campaign, null)
  assert.equal(hits[2].ep.method, 'email')
  assert.equal(hits[2].uid, '0123456789abcdef0123456789abcdef')
  assert.ok(hits.every((h) => h.cid === '123.456' && h.gcs === 'G111'))
})

test('items: list position and list fields', () => {
  assert.deepEqual(parseItem('idTL-STK-001~nmLogo Sticker~lp3~liall_products~lnAll products'), {
    item_id: 'TL-STK-001',
    item_name: 'Logo Sticker',
    index: 3,
    item_list_id: 'all_products',
    item_list_name: 'All products',
  })
})

test('campaignFromUrl: utm_* only, null without them or for junk', () => {
  assert.equal(campaignFromUrl('http://localhost/cart'), null)
  assert.equal(campaignFromUrl('not a url'), null)
  assert.deepEqual(campaignFromUrl('http://x/?utm_source=google&utm_medium=cpc&utm_campaign=fall_launch&utm_term=logo%20hoodie'), {
    source: 'google',
    medium: 'cpc',
    campaign: 'fall_launch',
    term: 'logo hoodie',
  })
})

test('consent state from gcs', () => {
  assert.equal(analyticsConsent('G111'), 'granted')
  assert.equal(analyticsConsent('G101'), 'granted')
  assert.equal(analyticsConsent('G100'), 'denied')
  assert.equal(analyticsConsent(undefined), 'unknown')
})

test('host rules: collect endpoints and the gtag.js script', () => {
  assert.ok(isCollect(new URL('https://region1.google-analytics.com/g/collect?v=2')))
  assert.ok(isCollect(new URL('https://www.google.com/g/collect?v=2')))
  assert.ok(!isCollect(new URL('https://localhost/g/collect')))
  assert.ok(isGtagScript(new URL('https://www.googletagmanager.com/gtag/js?id=G-X')))
  assert.ok(!isGtagScript(new URL('https://www.googletagmanager.com/td?id=G-X')))
})

test('findPii: raw, encoded and double-encoded emails, and the simulated people’s own addresses', () => {
  const email = 'tagline-shopper-007@tagline-sim.example'
  assert.deepEqual(findPii(`${base}&en=page_view`, [email]), [])
  assert.ok(findPii(`${base}&ep.search_term=jo@example.com`).length)
  assert.ok(findPii(`${base}&dl=${encodeURIComponent('http://x/?q=jo@example.com')}`).length)
  assert.ok(findPii(`${base}&dl=${encodeURIComponent(encodeURIComponent('http://x/?q=jo@example.com'))}`).length)
  assert.ok(findPii(`${base}&dt=TAGLINE-SHOPPER-007`, [email]).length, 'local part, any case')
  // the site's redaction marker is not personal data
  assert.deepEqual(findPii(`${base}&ep.search_term=%5Bemail%5D&dl=${encodeURIComponent('http://x/search?q=[email]')}`, [email]), [])
})

test('the account directory entry matches what site/src/auth/accounts.ts looks up', () => {
  const dir = accountDirectory(' Jo@Example.test ', '0123456789abcdef0123456789abcdef')
  const key = createHash('sha256').update('tagline-supply/account-lookup/v1:jo@example.test').digest('hex')
  assert.deepEqual(dir, { [key]: '0123456789abcdef0123456789abcdef' })
})
