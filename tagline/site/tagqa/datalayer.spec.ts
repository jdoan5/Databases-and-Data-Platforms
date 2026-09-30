/**
 * Tag QA, dataLayer layer: every journey in plan.ts on the Vite dev server (React
 * StrictMode on, so every effect runs twice), GA4 forwarding off.
 *
 * Each journey must pass every rule in rules.ts and match its golden snapshot; with no
 * measurement id, nothing may leave the machine and no GA4 forwarding command may be
 * pushed either (forwarding-off: the golden is written from the site's pushes, which
 * leave those commands out, so only this rule sees them here).
 */
import { test } from '@playwright/test'
import { checkJourney, forwardingOffRule } from './rules'
import { compareGolden, driveJourney, report, contract, toGolden } from './harness'
import { JOURNEYS } from './plan'

for (const journey of JOURNEYS) {
  test(`${journey.id}: ${journey.title}`, async ({ page, context }, testInfo) => {
    const offsite: string[] = []
    context.on('request', (req) => {
      const url = new URL(req.url())
      if (url.protocol !== 'data:' && url.hostname !== 'localhost') offsite.push(req.url())
    })

    const run = await driveJourney(page, context, journey)

    const violations = [
      ...checkJourney(run.all, { contract, expect: journey.expect, typed: journey.typed, userIds: journey.userIds }),
      ...forwardingOffRule(run.all),
      ...run.problems,
      ...offsite.map((url) => ({ rule: 'offsite', where: 'journey', message: `a request left the machine with GA4 forwarding off: ${url}` })),
    ]
    violations.push(...(await compareGolden(toGolden(journey, run.site), testInfo, { mayWrite: violations.length === 0, layer: 'datalayer' })))
    report(journey, 'dataLayer layer, dev server', violations, testInfo)
  })
}
