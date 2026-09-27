/**
 * The dry run, end to end: builds the site with the fake measurement id, drives real
 * Chrome through 8 seeded people (every channel, both sign-in kinds, all three consent
 * choices, a purchase, cross-device sign-ins, the email-in-search probe), and requires
 * every check in summary.js to pass. Needs Google Chrome installed and a network
 * connection to load gtag.js; no hits are sent to Google.
 *
 *   npm run test:e2e        (about 30 seconds)
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { runSimulation } from '../src/run.js'
import { formatSummary } from '../src/summary.js'

test('dry run: 8 people, every check passes, no hit reaches Google', { timeout: 240_000 }, async () => {
  const outDir = process.env.SIM_E2E_OUT ?? mkdtempSync(join(tmpdir(), 'tagline-sim-e2e-'))
  try {
    const summary = await runSimulation({ people: 8, seed: 'tagline', population: 'tagline', concurrency: 4, pace: 0.25, outDir, log: () => {} })
    console.log(formatSummary(summary))
    assert.deepEqual(summary.failures, [])
    assert.equal(summary.mode, 'dry-run')
    assert.equal(summary.leaks, 0)
    assert.equal(summary.pii.violations.length, 0)
    assert.ok(summary.hits > 50)
    assert.ok(summary.purchases.hits >= 1)
    assert.ok(summary.uid.peopleOnTwoPlusDevicesUnderOneUid >= 1)
    for (const c of ['fall_launch', 'newsletter_oct', 'retarget_q4', 'organic', 'direct']) {
      assert.ok(summary.channels[c]?.firstHit > 0, `a ${c} session attributed from its first hit`)
    }
    const hits = readFileSync(join(outDir, 'hits.ndjson'), 'utf8').trim().split('\n').map((l) => JSON.parse(l))
    assert.equal(hits.length, summary.hits)
    assert.ok(hits.every((h) => h.tid === 'G-DRYRUN0000'))
    assert.ok(hits.some((h) => h.en === 'search' && h.ep.search_term === '[email]'), 'the email typed into search went out redacted')
  } finally {
    if (!process.env.SIM_E2E_OUT && existsSync(outDir)) rmSync(outDir, { recursive: true, force: true })
  }
})
