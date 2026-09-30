/**
 * What the two layers' specs share: the contract, running a journey step by step,
 * the golden snapshots, and turning violations into one readable failure.
 */
import { test, type BrowserContext, type Page, type TestInfo } from '@playwright/test'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createContract, type JsonSchema } from '../src/tagging/contract.ts'
import { diffJourney } from './diff'
import { GOLDEN_NOTE, normalise, sitePushes, type Golden, type Recording } from './entries'
import { describeStep, Recorder, runStep, startsPageLoad } from './journey'
import type { Journey } from './plan'
import { formatViolations, type Violation } from './rules'

const schemaPath = new URL('../../tagging/events.schema.json', import.meta.url)
export const schema = JSON.parse(readFileSync(schemaPath, 'utf8')) as JsonSchema & { $defs: { event_name: { enum: string[] } } }
/** The same validator the site runs, compiled here from the file on disk. */
export const contract = createContract(schema)
/** The contract's 14 event names. */
export const CONTRACT_EVENTS: ReadonlySet<string> = new Set(schema.$defs.event_name.enum)

export interface JourneyRun {
  /** Every push the page made. */
  all: Recording
  /** The site's own pushes (without GA4-forwarding commands and gtm.* events). */
  site: Recording
  /** Uncaught page errors, and the site's own validator warning about a push. */
  problems: Violation[]
}

/**
 * After the last step, how long nothing may be pushed before the journey is judged. The
 * steps before it are followed by 300 ms (Recorder.settle); a push that comes later than
 * that after a middle step is still recorded by the next step's wait, but after the last
 * step nothing follows, so a late duplicate (a second purchase 700 ms on) needs the
 * longer window to be seen.
 */
export const FINAL_QUIET_MS = 1500

/**
 * Runs the journey's steps, each as a Playwright test step, settling the dataLayer after
 * each one, and after the last for FINAL_QUIET_MS. `beforeNewLoad` runs before every step
 * that starts a new page load (after the first), which is when the GA4 layer waits for
 * gtag.js to send what is queued.
 */
export async function driveJourney(
  page: Page,
  context: BrowserContext,
  journey: Journey,
  opts: { recorder?: Recorder; beforeNewLoad?: (recorder: Recorder) => Promise<void> } = {},
): Promise<JourneyRun> {
  const recorder = opts.recorder ?? new Recorder()
  if (!opts.recorder) await recorder.install(context)
  const problems: Violation[] = []
  page.on('pageerror', (err) => problems.push({ rule: 'page-error', where: page.url(), message: err.message }))
  page.on('console', (msg) => {
    // The site validates every push itself and warns with this prefix when one fails.
    if (msg.type() === 'warning' && msg.text().includes('[tagline]')) {
      problems.push({ rule: 'site-validator', where: page.url(), message: msg.text().slice(0, 300) })
    }
  })

  for (const [n, step] of journey.steps.entries()) {
    await test.step(`${n + 1}. ${describeStep(step)}`, async () => {
      if (n > 0 && startsPageLoad(step) && opts.beforeNewLoad) await opts.beforeNewLoad(recorder)
      await runStep(page, step)
      await recorder.settle(page, n === journey.steps.length - 1 ? FINAL_QUIET_MS : undefined)
    })
  }
  const all = recorder.recording()
  return { all, site: sitePushes(all), problems }
}

// ---- golden snapshots ----------------------------------------------------------------

const GOLDEN_DIR = new URL('./golden/', import.meta.url)
export const goldenFile = (id: string) => fileURLToPath(new URL(`${id}.json`, GOLDEN_DIR))
const goldenLabel = (id: string) => `tagqa/golden/${id}.json`

export function readGolden(id: string): Golden | null {
  const file = goldenFile(id)
  return existsSync(file) ? (JSON.parse(readFileSync(file, 'utf8')) as Golden) : null
}

export function toGolden(journey: Journey, site: Recording): Golden {
  return { journey: journey.id, title: journey.title, note: GOLDEN_NOTE, loads: normalise(site) }
}

const UPDATE_HINT = 'If the change is intended: npm run tagqa:update, review the golden diff, and commit it with the change.'

/**
 * Compares a run with its golden and, only when asked (npm run tagqa:update, i.e.
 * --update-snapshots) and only when every rule passed, rewrites it. A missing golden is
 * written on a local run but still fails it, so a new journey's first snapshot is looked
 * at before it is trusted; in CI nothing is ever written. The diff and the run's own
 * snapshot go into the test's output folder and the HTML report.
 */
export async function compareGolden(
  actual: Golden,
  testInfo: TestInfo,
  opts: { mayWrite: boolean; layer: string },
): Promise<Violation[]> {
  const id = actual.journey
  const mode = testInfo.config.updateSnapshots
  const inCI = !!process.env.CI
  const explicitUpdate = !inCI && (mode === 'all' || mode === 'changed')
  const canWrite = !inCI && mode !== 'none' && opts.mayWrite && opts.layer === 'datalayer'
  const write = () => {
    mkdirSync(fileURLToPath(GOLDEN_DIR), { recursive: true })
    writeFileSync(goldenFile(id), `${JSON.stringify(actual, null, 2)}\n`)
  }
  const actualPath = testInfo.outputPath(`${id}.actual.json`)
  const keepActual = () => writeFileSync(actualPath, `${JSON.stringify(actual, null, 2)}\n`)

  const existing = readGolden(id)
  if (!existing) {
    keepActual()
    if (!canWrite) {
      return [{ rule: 'golden', where: goldenLabel(id), message: `no golden snapshot yet. ${opts.layer === 'datalayer' ? 'Run npm run tagqa:update locally once every rule passes, check it, and commit it.' : 'The dataLayer layer writes it.'}` }]
    }
    write()
    if (explicitUpdate) {
      testInfo.annotations.push({ type: 'golden', description: `wrote ${goldenLabel(id)}` })
      return []
    }
    return [{ rule: 'golden', where: goldenLabel(id), message: 'no golden snapshot yet: wrote one from this run. Check it and commit it; the next run compares against it.' }]
  }

  const diff = diffJourney(existing.loads, actual.loads)
  if (diff.length === 0) {
    if (canWrite && mode === 'all' && (existing.title !== actual.title || existing.note !== actual.note)) write()
    return []
  }
  const text = diff.join('\n')
  keepActual()
  const diffPath = testInfo.outputPath(`${id}.golden.diff.txt`)
  writeFileSync(diffPath, `${goldenLabel(id)} (${opts.layer} layer)\n${text}\n`)
  await testInfo.attach(`${id}.golden.diff.txt`, { path: diffPath, contentType: 'text/plain' })
  await testInfo.attach(`${id}.actual.json`, { path: actualPath, contentType: 'application/json' })
  if (explicitUpdate && canWrite) {
    write()
    testInfo.annotations.push({ type: 'golden', description: `updated ${goldenLabel(id)}:\n${text}` })
    return []
  }
  const refused = explicitUpdate && opts.layer === 'datalayer' ? 'Not updated: a golden is only rewritten when every other rule passes.\n      ' : ''
  const where = opts.layer === 'datalayer' ? '' : ' (with GA4 forwarding on, in the production build)'
  return [
    {
      rule: 'golden',
      where: goldenLabel(id),
      message: `the site's pushes${where} differ from the golden:\n${text.replace(/^/gm, '      ')}\n      ${refused}${UPDATE_HINT}`,
    },
  ]
}

// ---- reporting -----------------------------------------------------------------------

/** One failure listing every broken rule; also written to the test's output folder (a CI artifact). */
export function report(journey: Journey, layer: string, violations: Violation[], testInfo: TestInfo): void {
  if (violations.length === 0) return
  const rules = [...new Set(violations.map((v) => v.rule))]
  const text = `tag QA: journey "${journey.id}" (${layer}) broke ${rules.length} rule(s): ${rules.join(', ')}\n${formatViolations(violations)}`
  writeFileSync(testInfo.outputPath('tag-qa-violations.txt'), `${text}\n`)
  // No stack: the message is the whole story, and a code frame of this file would only add noise.
  const error = new Error(text)
  error.stack = `Error: ${text}`
  throw error
}
