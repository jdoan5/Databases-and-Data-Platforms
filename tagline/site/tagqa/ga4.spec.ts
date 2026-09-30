/**
 * Tag QA, GA4 hit layer: every journey in plan.ts on a production build made with a
 * fake measurement id (G-TAGQA0000), so the site loads gtag.js and forwards each event.
 * No hit reaches Google: see ga4.ts.
 *
 * Checked: the hits against the dataLayer (ga4.ts, checkHits), every push against the
 * contract (gtag config/set/event commands included), the other rules on the site's
 * own pushes, and that those pushes still match the golden written with forwarding off,
 * so turning GA4 on, and building for production, changes nothing in the dataLayer.
 */
import { test } from '@playwright/test'
import { checkHits, Ga4Capture, MEASUREMENT_ID, siteEventCount } from './ga4'
import { sitePushes } from './entries'
import { CONTRACT_EVENTS, compareGolden, contract, driveJourney, report, toGolden } from './harness'
import { Recorder } from './journey'
import { JOURNEYS } from './plan'
import { checkJourney } from './rules'

for (const journey of JOURNEYS) {
  test(`${journey.id}: ${journey.title}`, async ({ page, context }, testInfo) => {
    // gtag.js sends a batch about 5 s after queueing its first event; a journey waits for
    // that once per page load.
    test.setTimeout(90_000)
    const capture = new Ga4Capture(CONTRACT_EVENTS)
    await capture.install(context)
    const recorder = new Recorder()
    await recorder.install(context)

    const flush = async () => {
      await test.step('wait for gtag.js to send what it has queued', () => capture.flush(page, siteEventCount(recorder.recording())))
    }
    const run = await driveJourney(page, context, journey, { recorder, beforeNewLoad: flush })
    await flush()
    // Read again after that last wait (seconds after the last step): a push that came even
    // later than the final quiet window is judged by every rule, not only counted as a hit.
    const all = recorder.recording()

    const violations = [
      ...checkHits(all, capture, { measurementId: MEASUREMENT_ID, typed: journey.typed }),
      ...checkJourney(all, { contract, expect: journey.expect, typed: journey.typed, userIds: journey.userIds }),
      ...run.problems,
    ]
    violations.push(...(await compareGolden(toGolden(journey, sitePushes(all)), testInfo, { mayWrite: false, layer: 'ga4' })))

    const own = Object.entries(capture.ownHits()).map(([name, n]) => `${name} ×${n}`)
    await testInfo.attach('ga4-hits.json', {
      body: JSON.stringify({ requests: capture.requests.length, hits: capture.hits, aborted: capture.aborted }, null, 2),
      contentType: 'application/json',
    })
    testInfo.annotations.push({
      type: 'ga4',
      description: `${capture.requests.length} /g/collect requests, ${capture.siteHits().length} site hits; gtag.js's own: ${own.join(', ') || 'none'}; other Google requests aborted: ${capture.aborted.length}`,
    })
    report(journey, 'GA4 hit layer, production build', violations, testInfo)
  })
}
