/**
 * What a run sent (or would have sent), checked against what was planned.
 *
 * Pure: takes the plan, the per-device results and the parsed hits, returns counts
 * plus two lists. `failures` make the CLI exit non-zero; `warnings` are worth reading
 * but do not mean the site or the simulator is wrong.
 */
import { CAMPAIGNS } from './plan.js'
import { analyticsConsent } from './hits.js'

/** Events gtag.js sends on its own, not forwarded from the dataLayer. */
const AUTOMATIC_EVENTS = new Set(['user_engagement', 'scroll', 'click', 'view_search_results', 'file_download', 'form_start', 'form_submit', 'video_start'])

const countBy = (rows, key) => {
  const out = {}
  for (const r of rows) {
    const k = key(r) ?? '(none)'
    out[k] = (out[k] ?? 0) + 1
  }
  return Object.fromEntries(Object.entries(out).sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))))
}

const hostOf = (u) => {
  try {
    return new URL(u).hostname
  } catch {
    return null
  }
}

/** The channel a hit implies, read the way GA4 reads a session's first event. */
export function channelOf(hit) {
  if (hit.campaign?.campaign) return hit.campaign.campaign
  const ref = hostOf(hit.dr)
  if (ref && /(^|\.)google\.[a-z.]+$/.test(ref)) return 'organic'
  if (ref && ref !== hostOf(hit.dl)) return `referral:${ref}`
  return 'direct'
}

const round2 = (n) => Math.round(n * 100) / 100

export function summarise({ plan, devices, hits, requests, leaks = [], mode, measurementId, headless, seconds }) {
  const failures = []
  const warnings = []
  const people = new Map(plan.people.map((p) => [p.personId, p]))
  const plannedDevice = new Map(plan.people.flatMap((p) => p.devices.map((d) => [d.deviceId, d])))
  const hitsBy = new Map()
  for (const h of hits) {
    if (!hitsBy.has(h.device_id)) hitsBy.set(h.device_id, [])
    hitsBy.get(h.device_id).push(h)
  }

  // --- runs that did not finish
  for (const d of devices) for (const e of d.errors) failures.push(`${d.deviceId}: ${e}`)
  const ran = new Set(devices.map((d) => d.deviceId))
  for (const id of plannedDevice.keys()) if (!ran.has(id)) failures.push(`${id}: planned but never ran`)

  // --- personal data and leaks
  const pii = requests.filter((r) => r.pii.length).map((r) => ({ request: r.i, device: r.device_id, findings: r.pii }))
  for (const p of pii) failures.push(`personal data in request ${p.request} (${p.device}): ${p.findings.join('; ')}`)
  if (mode === 'dry-run') for (const l of leaks) failures.push(`dry run: a Google request completed instead of being aborted: ${l.url.slice(0, 120)}`)

  // --- per device: attribution, identity, consent, forwarding completeness
  const channels = {}
  const row = (c) => (channels[c] ??= { planned: 0, firstHit: 0, firstConsentedHit: 0, consentedDevices: 0, orders: 0, revenue: 0 })
  const uidDevices = new Map() // personId → Map(deviceId → cid set)
  for (const d of devices) {
    if (d.errors.length) continue
    const want = plannedDevice.get(d.deviceId)
    const person = people.get(d.personId)
    const dh = hitsBy.get(d.deviceId) ?? []
    const r = row(want.channel)
    r.planned++
    if (dh.length === 0) {
      failures.push(`${d.deviceId}: no hits at all`)
      continue
    }

    // Attribution: the first hit must carry the landing page's UTM (or referrer).
    const first = dh[0]
    const seen = channelOf(first)
    if (seen === want.channel) r.firstHit++
    else failures.push(`${d.deviceId}: planned ${want.channel}, first hit reads as ${seen} (dl=${first.dl}, dr=${first.dr ?? ''})`)
    const c = CAMPAIGNS[want.channel]
    if (c && (first.campaign?.source !== c.source || first.campaign?.medium !== c.medium)) {
      failures.push(`${d.deviceId}: first hit has utm_source/medium ${first.campaign?.source}/${first.campaign?.medium}, planned ${c.source}/${c.medium}`)
    }
    // With consent granted part-way through the page, GA4 starts a fresh cookie-based
    // session at the first consented hit, so that hit's page URL decides attribution.
    const firstConsented = dh.find((h) => analyticsConsent(h.gcs) === 'granted')
    if (firstConsented) {
      r.consentedDevices++
      const seenConsented = channelOf(firstConsented)
      if (seenConsented === want.channel) r.firstConsentedHit++
      else warnings.push(`${d.deviceId}: first consented hit (${firstConsented.en}) reads as ${seenConsented}, planned ${want.channel}`)
    }

    // Identity: uid only after this device signed in, and always the person's account id.
    const signInAt = dh.findIndex((h) => h.en === 'login' || h.en === 'sign_up')
    dh.forEach((h, i) => {
      if (!h.uid) return
      if (h.uid !== person.accountId) failures.push(`${d.deviceId}: hit ${h.i} carries uid ${h.uid}, not the person's account id`)
      if (!want.signIn) failures.push(`${d.deviceId}: hit ${h.i} (${h.en}) carries a uid but the device never signed in`)
      else if (signInAt >= 0 && i < signInAt) failures.push(`${d.deviceId}: hit ${h.i} (${h.en}) carries a uid before the ${want.signIn}`)
    })
    if (want.signIn) {
      const ev = dh.filter((h) => h.en === want.signIn)
      if (ev.length !== 1) failures.push(`${d.deviceId}: expected one ${want.signIn} hit, saw ${ev.length}`)
      else if (ev[0].uid !== person.accountId) failures.push(`${d.deviceId}: the ${want.signIn} hit has uid ${ev[0].uid ?? '(none)'}`)
      if (d.userId !== person.accountId) failures.push(`${d.deviceId}: the site showed account id ${d.userId}, planned ${person.accountId}`)
      if (!uidDevices.has(d.personId)) uidDevices.set(d.personId, new Map())
      uidDevices.get(d.personId).set(d.deviceId, new Set(dh.filter((h) => h.uid).map((h) => h.cid)))
    }

    // Consent: a visitor who rejected or ignored the banner never has a granted hit.
    const granted = dh.filter((h) => analyticsConsent(h.gcs) === 'granted').length
    if (want.consent !== 'accept' && granted) failures.push(`${d.deviceId}: consent ${want.consent}, but ${granted} hits say analytics_storage granted`)
    if (want.consent === 'accept') {
      const after = dh.slice(dh.indexOf(firstConsented))
      if (!firstConsented) warnings.push(`${d.deviceId}: accepted consent but sent no consented hit`)
      else if (after.some((h) => analyticsConsent(h.gcs) !== 'granted')) failures.push(`${d.deviceId}: a denied hit after consent was granted`)
    }

    // Forwarding: every event the site pushed reached gtag.js's requests, once.
    const pushed = countBy(d.dataLayerEvents.map((e) => ({ e })), (x) => x.e)
    const sent = countBy(dh, (h) => h.en)
    for (const [name, n] of Object.entries(pushed)) {
      if ((sent[name] ?? 0) !== n) failures.push(`${d.deviceId}: ${name} pushed ${n}× to the dataLayer, sent ${sent[name] ?? 0}×`)
    }
    for (const name of Object.keys(sent)) {
      if (!(name in pushed) && !AUTOMATIC_EVENTS.has(name)) warnings.push(`${d.deviceId}: ${name} sent but never pushed by the site`)
    }

    // Orders
    for (const h of dh.filter((x) => x.en === 'purchase')) {
      r.orders++
      r.revenue = round2(r.revenue + (h.epn.value ?? 0))
    }
  }

  // --- purchases
  const purchaseHits = hits.filter((h) => h.en === 'purchase')
  const txIds = purchaseHits.map((h) => h.ep.transaction_id)
  const observedTx = devices.flatMap((d) => d.transactionIds)
  const plannedPurchases = [...plannedDevice.values()].filter((d) => d.steps.some((s) => s.do === 'place_order')).length
  if (new Set(txIds).size !== txIds.length) failures.push('a transaction_id was sent twice')
  if (txIds.length !== plannedPurchases) failures.push(`${plannedPurchases} purchases planned, ${txIds.length} purchase hits`)
  for (const t of observedTx) if (!txIds.includes(t)) failures.push(`order ${t} was placed but no purchase hit carries it`)

  // --- cross-device identity
  let crossDevice = 0
  for (const [personId, devs] of uidDevices) {
    if (devs.size < 2) continue
    const cids = new Set([...devs.values()].flatMap((s) => [...s]))
    if (cids.size >= 2) crossDevice++
    else warnings.push(`${personId}: signed in on ${devs.size} devices but only ${cids.size} distinct client id carried the uid`)
  }
  const plannedCrossDevice = plan.people.filter((p) => p.devices.filter((d) => d.signIn).length > 1).length

  // --- retries: the same request line seen twice
  const seenKeys = new Map()
  for (const h of hits) {
    const key = `${h.device_id}|${h.page_load_id}|${h.seq}|${h.line}|${h.en}`
    seenKeys.set(key, (seenKeys.get(key) ?? 0) + 1)
  }
  const repeats = [...seenKeys.values()].filter((n) => n > 1).length
  if (repeats) warnings.push(`${repeats} hit(s) appear more than once with the same page load, sequence and line (gtag.js retries)`)

  const collect = requests.filter((r) => r.collect)
  const other = requests.filter((r) => !r.collect)
  return {
    mode,
    measurementId,
    browser: headless ? 'headless' : 'headed',
    seconds: seconds === undefined ? undefined : Math.round(seconds),
    seed: plan.seed,
    population: plan.population,
    people: plan.people.length,
    devices: devices.length,
    requests: { collect: collect.length, other: other.length, byHost: countBy(collect, (r) => r.host), transports: countBy(collect, (r) => `${r.method} ${r.resource_type}`), otherGoogle: countBy(other, (r) => `${r.host}${r.path}`) },
    hits: hits.length,
    byEvent: countBy(hits, (h) => h.en),
    byConsentState: countBy(hits, (h) => h.gcs),
    hitsByUtmCampaign: countBy(hits, (h) => h.campaign?.campaign ?? '(no utm in dl)'),
    channels,
    uid: {
      hitsWithUid: hits.filter((h) => h.uid).length,
      devicesWithUid: new Set(hits.filter((h) => h.uid).map((h) => h.device_id)).size,
      distinctUids: new Set(hits.filter((h) => h.uid).map((h) => h.uid)).size,
      peopleOnTwoPlusDevicesUnderOneUid: crossDevice,
      plannedCrossDevicePeople: plannedCrossDevice,
    },
    clientIds: new Set(hits.map((h) => h.cid).filter(Boolean)).size,
    hitsWithoutCid: hits.filter((h) => !h.cid).length,
    purchases: {
      planned: plannedPurchases,
      hits: purchaseHits.length,
      distinctTransactionIds: new Set(txIds).size,
      value: round2(purchaseHits.reduce((s, h) => s + (h.epn.value ?? 0), 0)),
      tax: round2(purchaseHits.reduce((s, h) => s + (h.epn.tax ?? 0), 0)),
      shipping: round2(purchaseHits.reduce((s, h) => s + (h.epn.shipping ?? 0), 0)),
    },
    pii: { requestsChecked: requests.length, violations: pii },
    leaks: leaks.length,
    warnings,
    failures,
  }
}

const table = (obj, indent = '    ') =>
  Object.entries(obj)
    .map(([k, v]) => `${indent}${String(k).padEnd(28)} ${v}`)
    .join('\n')

export function formatSummary(s) {
  const lines = []
  lines.push(`\n${s.mode === 'dry-run' ? 'DRY RUN: no hits were sent to Google; gtag.js was downloaded once' : `LIVE: sent to GA4 property stream ${s.measurementId}`} (${s.browser} Chrome${s.seconds !== undefined ? `, ${s.seconds}s` : ''})`)
  lines.push(`  people ${s.people}, devices ${s.devices}, /g/collect requests ${s.requests.collect}, hits ${s.hits}, client ids ${s.clientIds} (+${s.hitsWithoutCid} hits without cid)`)
  lines.push(`  collection hosts:\n${table(s.requests.byHost)}`)
  lines.push(`  transports:\n${table(s.requests.transports)}`)
  if (Object.keys(s.requests.otherGoogle).length) lines.push(`  other Google requests (${s.mode === 'dry-run' ? 'aborted' : 'sent'}):\n${table(s.requests.otherGoogle)}`)
  lines.push(`  hits per event:\n${table(s.byEvent)}`)
  lines.push(`  hits per consent state (gcs: G1<ad_storage><analytics_storage>):\n${table(s.byConsentState)}`)
  lines.push(`  hits per utm_campaign in dl:\n${table(s.hitsByUtmCampaign)}`)
  lines.push('  sessions per channel: planned / first hit matches / first consented hit matches (of devices with one) / orders / revenue')
  for (const [c, r] of Object.entries(s.channels).sort()) {
    lines.push(`    ${c.padEnd(16)} ${String(r.planned).padStart(3)} / ${String(r.firstHit).padStart(3)} / ${String(r.firstConsentedHit).padStart(3)} of ${String(r.consentedDevices).padStart(2)} / ${String(r.orders).padStart(2)} / ${r.revenue.toFixed(2)}`)
  }
  const u = s.uid
  lines.push(`  user_id: ${u.hitsWithUid} hits on ${u.devicesWithUid} devices carry uid (${u.distinctUids} distinct); ${u.peopleOnTwoPlusDevicesUnderOneUid} of ${u.plannedCrossDevicePeople} planned people seen on 2+ devices under one uid`)
  const p = s.purchases
  lines.push(`  purchases: ${p.hits} hits (${p.planned} planned, ${p.distinctTransactionIds} distinct transaction_id), value ${p.value.toFixed(2)}, tax ${p.tax.toFixed(2)}, shipping ${p.shipping.toFixed(2)}`)
  lines.push(`  personal data: ${s.pii.violations.length} of ${s.pii.requestsChecked} requests carry an email or a simulated person's email`)
  if (s.mode === 'dry-run') lines.push(`  leaks: ${s.leaks} Google requests completed (must be 0)`)
  if (s.warnings.length) lines.push(`  warnings (${s.warnings.length}):\n${s.warnings.slice(0, 25).map((w) => `    - ${w}`).join('\n')}${s.warnings.length > 25 ? `\n    … ${s.warnings.length - 25} more in summary.json` : ''}`)
  lines.push(s.failures.length ? `  FAILED (${s.failures.length}):\n${s.failures.slice(0, 40).map((f) => `    - ${f}`).join('\n')}` : '  all checks passed')
  return lines.join('\n')
}
