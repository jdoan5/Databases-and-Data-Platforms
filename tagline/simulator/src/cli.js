#!/usr/bin/env node
/**
 * tagline simulator: seeded synthetic shoppers for the Tagline site.
 *
 *   node src/cli.js                      dry run: 50 people, no hits sent to Google
 *   node src/cli.js --plan-only          print the plan's counts and stop
 *   node src/cli.js --live --measurement-id G-XXXXXXXXXX
 *                                        send synthetic traffic to that GA4 property
 */
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { DEFAULTS, buildPlan, planStats } from './plan.js'
import { runSimulation } from './run.js'
import { formatSummary } from './summary.js'

const HELP = `Usage: npm run simulate -- [options]

Dry run by default: the site runs with a fake measurement id (G-DRYRUN0000); each
/g/collect hit is recorded and answered locally with a 204, other Google requests are
aborted, and a summary says what would have been sent. gtag.js itself is downloaded
from Google once per run (no hit data). Live mode sends synthetic traffic to a real GA4
property.

  --people N            people to simulate (default ${DEFAULTS.people})
  --seed S              journey seed: same seed, same plan (default "${DEFAULTS.seed}")
  --population P        who the customers are; same population, same account ids
                        across runs (default "${DEFAULTS.population}")
  --concurrency N       people simulated at once (default 4)
  --pace F              multiply every pause by F, e.g. 0.3 for a quick check (default 1)
  --server preview|dev  production build + vite preview (default), or the dev server
  --out DIR             where plan.json, hits.ndjson, summary.json go (default simulator/out/<mode>)
  --headless / --headed Chrome mode (default: headless for a dry run, headed for live)
  --plan-only           print the plan's counts (and --out DIR/plan.json) without a browser
  --live                send to GA4 for real; needs --measurement-id or VITE_GA4_MEASUREMENT_ID
  --measurement-id ID   the GA4 web stream's measurement id, G-XXXXXXXXXX
  --yes                 skip the 10-second countdown before a live run
  --abort-collect       dry run: abort each /g/collect request instead of answering it
                        locally with a 204 (shows how gtag.js reacts to failed hits)
  -h, --help            this text
`

function fail(message) {
  console.error(`error: ${message}\n\n${HELP}`)
  process.exit(2)
}

let args
try {
  args = parseArgs({
    options: {
      people: { type: 'string' },
      seed: { type: 'string' },
      population: { type: 'string' },
      concurrency: { type: 'string' },
      pace: { type: 'string' },
      server: { type: 'string' },
      out: { type: 'string' },
      headless: { type: 'boolean' },
      headed: { type: 'boolean' },
      'plan-only': { type: 'boolean' },
      live: { type: 'boolean' },
      'measurement-id': { type: 'string' },
      yes: { type: 'boolean' },
      'abort-collect': { type: 'boolean' },
      help: { type: 'boolean', short: 'h' },
    },
    strict: true,
  }).values
} catch (err) {
  fail(err.message)
}
if (args.help) {
  console.log(HELP)
  process.exit(0)
}

const positiveInt = (v, name, fallback) => {
  if (v === undefined) return fallback
  const n = Number(v)
  if (!Number.isInteger(n) || n < 1) fail(`--${name} must be a positive integer`)
  return n
}
const people = positiveInt(args.people, 'people', DEFAULTS.people)
const concurrency = positiveInt(args.concurrency, 'concurrency', 4)
const pace = args.pace === undefined ? 1 : Number(args.pace)
if (!(pace >= 0 && pace <= 10)) fail('--pace must be a number from 0 to 10')
const seed = args.seed ?? DEFAULTS.seed
const population = args.population ?? DEFAULTS.population
if (args.headless && args.headed) fail('choose --headless or --headed, not both')

const live = Boolean(args.live)
const measurementId = (args['measurement-id'] ?? (live ? process.env.VITE_GA4_MEASUREMENT_ID : '') ?? '').trim()
if (live && !/^G-[A-Z0-9]{4,}$/.test(measurementId)) fail('--live needs a GA4 measurement id: --measurement-id G-XXXXXXXXXX (or VITE_GA4_MEASUREMENT_ID)')
if (live && args['abort-collect']) fail('--abort-collect is for dry runs only')
if (!live && args['measurement-id']) fail('--measurement-id is only used with --live; a dry run always uses G-DRYRUN0000')
if (!live && process.env.VITE_GA4_MEASUREMENT_ID) {
  console.log('note: VITE_GA4_MEASUREMENT_ID is set in the environment but --live was not given; this is a dry run with G-DRYRUN0000.')
}

const outDir = args.out ?? join(fileURLToPath(new URL('../out/', import.meta.url)), live ? 'live' : 'dry-run')

if (args['plan-only']) {
  const plan = buildPlan({ people, seed, population })
  console.log(JSON.stringify(planStats(plan), null, 2))
  if (args.out) {
    const { mkdirSync, writeFileSync } = await import('node:fs')
    mkdirSync(outDir, { recursive: true })
    writeFileSync(join(outDir, 'plan.json'), JSON.stringify(plan, null, 2))
    console.log(`plan written to ${join(outDir, 'plan.json')}`)
  }
  process.exit(0)
}

const headless = args.headless ? true : args.headed ? false : !live

if (live) {
  const bar = '='.repeat(78)
  console.log(`${bar}
LIVE MODE: this sends SYNTHETIC traffic to the GA4 property behind ${measurementId}.
  - ${people} simulated people with made-up journeys, campaigns and orders. None of it
    is real behaviour; every order is fake. It will show up in that property's reports
    and its BigQuery export. Taking it out later needs a GA4 data-deletion request,
    and rows already exported to BigQuery stay until you delete them there.
  - Hostname is localhost; campaigns are fall_launch, newsletter_oct, retarget_q4.
  - Use a property made for this project, not one that holds real traffic.
${headless ? '  - HEADLESS was requested: GA4 may exclude it as automated traffic (see README).\n' : '  - Headed Chrome windows will open; leave them alone until the run ends.\n'}${bar}`)
  if (!args.yes) {
    for (let s = 10; s > 0; s--) {
      process.stdout.write(`\rstarting in ${s}s, Ctrl-C to cancel `)
      await new Promise((r) => setTimeout(r, 1000))
    }
    process.stdout.write('\n')
  }
}

try {
  const summary = await runSimulation({ people, seed, population, concurrency, pace, live, measurementId, headless, server: args.server ?? 'preview', abortCollect: Boolean(args['abort-collect']), outDir })
  console.log(formatSummary(summary))
  console.log(`\nwritten: ${outDir}/{plan.json,hits.ndjson,requests.ndjson,devices.ndjson,summary.json}`)
  process.exit(summary.failures.length ? 1 : 0)
} catch (err) {
  console.error(`error: ${err.stack ?? err.message}`)
  process.exit(2)
}
