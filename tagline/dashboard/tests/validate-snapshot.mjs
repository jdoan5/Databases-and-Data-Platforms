// Validates data/snapshot.json (or the file given as the first argument): the JSON Schema, the size budget, that it
// carries no identifier or configured name, and that its numbers agree with each other. Exit 1 on any failure.
//
//   node tests/validate-snapshot.mjs [path/to/snapshot.json]

import { readFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'

const here = dirname(fileURLToPath(import.meta.url))
const dashboard = resolve(here, '..')
const file = resolve(process.argv[2] || resolve(dashboard, 'data/snapshot.json'))
const text = readFileSync(file, 'utf8')
const snap = JSON.parse(text)
const schema = JSON.parse(readFileSync(resolve(dashboard, 'snapshot.schema.json'), 'utf8'))
const failures = []
const fail = (msg) => failures.push(msg)
const MAX_BYTES = 200 * 1024

// 1. the schema
const ajv = new Ajv2020({ allErrors: true, strict: true })
const validate = ajv.compile(schema)
if (!validate(snap)) for (const e of validate.errors) fail(`schema: ${e.instancePath || '/'} ${e.message}`)

// 2. small
const bytes = Buffer.byteLength(text)
if (bytes > MAX_BYTES) fail(`size: ${bytes} bytes, over the ${MAX_BYTES} budget`)

// 3. no identifiers: no id-shaped keys, no email, no GA4 measurement or property id, nothing from tagline/.env
const ID_KEYS = /^(user_id|user_pseudo_id|person_id|device_id|order_id|transaction_id|session_key|event_key|ga_session_id|client_id)$/
;(function walk(node, path) {
  if (Array.isArray(node)) node.forEach((v, i) => walk(v, `${path}/${i}`))
  else if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) {
      if (ID_KEYS.test(k)) fail(`privacy: identifier key ${path}/${k}`)
      walk(v, `${path}/${k}`)
    }
  }
})(snap, '')
const patterns = [
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, 'an email address'],
  [/\bG-[A-Z0-9]{6,}\b/, 'a GA4 measurement id'],
  [/analytics_\d+/, "a GA4 export dataset name (the property id)"],
  [/\b[0-9a-f]{32}\b/, 'a 32-hex account id'],
  [/iam\.gserviceaccount\.com/, 'a service account'],
]
for (const [re, what] of patterns) if (re.test(text)) fail(`privacy: contains ${what}: ${text.match(re)[0].slice(0, 12)}...`)
const envFile = resolve(dashboard, '../.env')
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, 'utf8').split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?(TAGLINE_[A-Z_]+)\s*=\s*["']?([^"'#\s]+)/)
    if (!m || m[1] === 'TAGLINE_MAX_BYTES_BILLED' || m[1] === 'TAGLINE_GCP_REGION' || m[2].length < 6) continue
    if (text.includes(m[2])) fail(`privacy: contains the value of ${m[1]} from tagline/.env`)
  }
}

// 4. the numbers agree with each other
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b))
if (!validate.errors) {
  for (const s of ['ga4_sample', 'tagline_site']) {
    const k = snap.kpis[s]
    const days = snap.daily[s]
    const sum = (key) => days.reduce((t, d) => t + (d[key] || 0), 0)
    if (sum('sessions') !== k.sessions) fail(`${s}: daily sessions ${sum('sessions')} != KPI total ${k.sessions}`)
    if (sum('orders') !== k.orders) fail(`${s}: daily orders ${sum('orders')} != KPI total ${k.orders}`)
    if (!near(sum('revenue_usd'), k.revenue_usd, 1e-9)) fail(`${s}: daily revenue != KPI total`)
    if (days.length !== k.days || snap.sources[s].days !== k.days) fail(`${s}: day counts disagree`)
    if (snap.sources[s].first_date !== days[0].date || snap.sources[s].last_date !== days.at(-1).date) fail(`${s}: date range disagrees with the daily rows`)
    if (snap.funnel[s].sessions !== k.sessions) fail(`${s}: funnel sessions != KPI sessions`)
    if (snap.funnel[s].converted !== k.converted_sessions) fail(`${s}: funnel converted != KPI converted sessions`)
    const h = snap.tag_health.by_source[s]
    if (h.pass + h.expected + h.violation !== h.rows) fail(`${s}: tag health statuses do not add up to its rows`)
    const byKind = h.by_kind.reduce((t, x) => t + x.pass + x.expected + x.violation, 0)
    if (byKind !== h.rows) fail(`${s}: tag health by kind does not add up to its rows`)
  }
  for (const s of ['ga4_sample', 'tagline_site']) {
    const a = snap.attribution[s]
    for (const m of snap.attribution.models) {
      const share = a.channels.reduce((t, c) => t + c.share[m], 0)
      if (!near(share, 1, 1e-4)) fail(`attribution ${s} ${m}: shares sum to ${share}`)
      const rev = a.channels.reduce((t, c) => t + c.revenue_usd[m], 0)
      if (!near(rev, a.totals_by_model[m].revenue_usd, 1e-6)) fail(`attribution ${s} ${m}: channel revenue != total`)
      if (!near(a.totals_by_model[m].revenue_usd, a.revenue_usd, 1e-6)) fail(`attribution ${s} ${m}: revenue differs between models`)
    }
  }
  const look = snap.attribution.lookback.ga4_sample
  if (!near(look.complete_revenue_usd, snap.attribution.ga4_sample.revenue_usd)) fail('attribution: sample revenue != complete-lookback revenue')
  if (!near(look.complete_revenue_usd + look.incomplete_revenue_usd, snap.kpis.ga4_sample.revenue_usd)) fail('attribution: sample lookback revenue != KPI revenue')
  const al = snap.alerts
  if (al.days.reduce((t, d) => t + d.count, 0) !== al.total) fail('alerts: day counts do not add up to the total')
  if (al.days.length !== al.source_days) fail('alerts: source_days != days listed')
  if (al.days.some((d) => d.items.length !== d.count || d.critical + d.warning !== d.count)) fail('alerts: a day\'s items disagree with its counts')
  const c = snap.campaigns.tagline_site
  if (c.rows.reduce((t, r) => t + r.sessions, 0) !== snap.kpis.tagline_site.sessions) fail('campaigns: site sessions != KPI sessions')
  if (c.totals.orders + snap.kpis.tagline_site.cookieless_orders !== snap.kpis.tagline_site.orders) fail('campaigns: orders in sessions + cookieless != KPI orders')
}

if (failures.length) {
  console.error(`snapshot ${file}: ${failures.length} problem(s)`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`snapshot ok: ${file.replace(`${dashboard}/`, '')}, ${bytes.toLocaleString('en-US')} bytes, schema v${snap.schema_version}, generated ${snap.generated_at}`)
