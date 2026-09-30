/**
 * A readable diff between a golden snapshot and what a journey pushed: per page load,
 * per push, per parameter.
 *
 * Pushes are aligned on their description (event name, `{ ecommerce: null }`,
 * `consent default` …) with a longest common subsequence, so one push too many or too
 * few shows as that one push, not as every later push shifted by one. Pushes that align
 * are compared parameter by parameter.
 */
import { describe, type Push } from './entries'

type Rec = Record<string, unknown>
const isRecord = (x: unknown): x is Rec => typeof x === 'object' && x !== null && !Array.isArray(x)

const MAX = 110
const show = (v: unknown): string => {
  const s = v === undefined ? '(missing)' : JSON.stringify(v)
  return s.length > MAX ? `${s.slice(0, MAX - 1)}…` : s
}

const join = (path: string, key: string | number) =>
  typeof key === 'number' ? `${path}[${key}]` : path ? `${path}.${key}` : key

/** Parameter-level differences between two pushes, as "path: expected X, got Y" lines. */
export function paramDiff(expected: unknown, actual: unknown, path = ''): string[] {
  const at = path || '(push)'
  if (Array.isArray(expected) && Array.isArray(actual)) {
    const out: string[] = []
    // More than a couple of entries short or over: say so once, compare what both have.
    const summarise = Math.abs(expected.length - actual.length) > 2
    if (summarise) out.push(`${path || '(push)'}: expected ${expected.length} entries, got ${actual.length}`)
    for (let i = 0; i < (summarise ? Math.min(expected.length, actual.length) : Math.max(expected.length, actual.length)); i++) {
      if (i >= actual.length) out.push(`${join(path, i)}: expected ${show(expected[i])}, got (missing)`)
      else if (i >= expected.length) out.push(`${join(path, i)}: unexpected ${show(actual[i])}`)
      else out.push(...paramDiff(expected[i], actual[i], join(path, i)))
    }
    return out
  }
  if (isRecord(expected) && isRecord(actual)) {
    const out: string[] = []
    for (const k of Object.keys(expected)) {
      if (!(k in actual)) out.push(`${join(path, k)}: expected ${show(expected[k])}, got (missing)`)
      else out.push(...paramDiff(expected[k], actual[k], join(path, k)))
    }
    for (const k of Object.keys(actual)) if (!(k in expected)) out.push(`${join(path, k)}: unexpected ${show(actual[k])}`)
    return out
  }
  if (JSON.stringify(expected) === JSON.stringify(actual)) return []
  return [`${at}: expected ${show(expected)}, got ${show(actual)}`]
}

export type Op = { kind: 'same'; e: number; a: number } | { kind: 'missing'; e: number } | { kind: 'extra'; a: number }

/** LCS alignment of two lists of keys. */
export function align(expected: string[], actual: string[]): Op[] {
  const n = expected.length
  const m = actual.length
  const lcs = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = expected[i] === actual[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
  const ops: Op[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (expected[i] === actual[j]) ops.push({ kind: 'same', e: i++, a: j++ })
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) ops.push({ kind: 'missing', e: i++ })
    else ops.push({ kind: 'extra', a: j++ })
  }
  while (i < n) ops.push({ kind: 'missing', e: i++ })
  while (j < m) ops.push({ kind: 'extra', a: j++ })
  return ops
}

/**
 * The key pushes are aligned on: the description, plus the URL for a page_view, so a
 * page_view too many shows as that one push even when a page_view for another page
 * follows it.
 */
const alignKey = (p: Push) => (describe(p) === 'page_view' && isRecord(p) ? `page_view ${String(p.page_location)}` : describe(p))

type Entry = { kind: 'changed' | 'moved'; e: number; a: number } | { kind: 'missing'; e: number } | { kind: 'extra'; a: number }

/**
 * Diff of one page load; [] when identical. `#n` is a 0-based dataLayer position; a push
 * that is missing is numbered by its place in the golden (`golden #n`). A push that is
 * missing in one place and pushed, unchanged, in another shows once, as moved.
 */
export function diffLoad(expected: Push[], actual: Push[]): string[] {
  const ops = align(expected.map(alignKey), actual.map(alignKey))
  const entries: Entry[] = []
  for (let i = 0; i < ops.length; ) {
    const op = ops[i]
    if (op.kind === 'same') {
      entries.push({ kind: 'changed', e: op.e, a: op.a })
      i++
      continue
    }
    // A run of unaligned pushes: a missing and an unexpected push of the same kind (a
    // page_view whose URL changed) are one changed push, compared parameter by parameter.
    let j = i
    while (j < ops.length && ops[j].kind !== 'same') j++
    const block = ops.slice(i, j)
    const pairs = new Map<number, number>()
    const used = new Set<number>()
    for (const m of block) {
      if (m.kind !== 'missing') continue
      const x = block.find((o) => o.kind === 'extra' && !used.has(o.a) && describe(actual[o.a]) === describe(expected[m.e]))
      if (x && x.kind === 'extra') {
        pairs.set(m.e, x.a)
        used.add(x.a)
      }
    }
    for (const o of block) {
      if (o.kind === 'missing') entries.push(pairs.has(o.e) ? { kind: 'changed', e: o.e, a: pairs.get(o.e)! } : { kind: 'missing', e: o.e })
      else if (o.kind === 'extra' && !used.has(o.a)) entries.push({ kind: 'extra', a: o.a })
    }
    i = j
  }
  // Across the whole load: a missing push and an unexpected one that are identical are the
  // same push in another place (a reorder), reported once where it now is.
  const movedFrom = new Set<number>()
  for (const [k, x] of entries.entries()) {
    if (x.kind !== 'extra') continue
    const m = entries.find((y) => y.kind === 'missing' && !movedFrom.has(y.e) && JSON.stringify(expected[y.e]) === JSON.stringify(actual[x.a]))
    if (m && m.kind === 'missing') {
      movedFrom.add(m.e)
      entries[k] = { kind: 'moved', e: m.e, a: x.a }
    }
  }
  const out: string[] = []
  for (const x of entries) {
    if (x.kind === 'changed') {
      const lines = paramDiff(expected[x.e], actual[x.a])
      if (lines.length) out.push(`  ~ #${x.a} ${describe(actual[x.a])}`, ...lines.map((l) => `        ${l}`))
    } else if (x.kind === 'moved') out.push(`  ↕ #${x.a} ${describe(actual[x.a])}   moved (golden #${x.e})`)
    else if (x.kind === 'missing' && !movedFrom.has(x.e)) out.push(`  - golden #${x.e} ${describe(expected[x.e])}   missing (in the golden, not pushed)`, `        ${show(expected[x.e])}`)
    else if (x.kind === 'extra') out.push(`  + #${x.a} ${describe(actual[x.a])}   unexpected (pushed, not in the golden)`, `        ${show(actual[x.a])}`)
  }
  return out
}

/** Diff of a whole journey; [] when identical. */
export function diffJourney(expected: { pushes: Push[] }[], actual: { pushes: Push[] }[]): string[] {
  const out: string[] = []
  const loads = Math.max(expected.length, actual.length)
  if (expected.length !== actual.length) out.push(`page loads: expected ${expected.length}, got ${actual.length}`)
  for (let i = 0; i < loads; i++) {
    const e = expected[i]?.pushes ?? []
    const a = actual[i]?.pushes ?? []
    const lines = diffLoad(e, a)
    if (lines.length) out.push(`load ${i + 1} (golden ${e.length} pushes, actual ${a.length}):`, ...lines)
  }
  return out
}
