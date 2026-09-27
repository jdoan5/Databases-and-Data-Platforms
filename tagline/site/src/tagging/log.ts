/**
 * In-page record of every dataLayer push and its validation result.
 * The Tag Inspector reads it; nothing else does.
 */
import type { Check } from './contract'

export interface LogEntry extends Check {
  seq: number
  /** epoch ms */
  at: number
  /** JSON-safe copy of what was pushed (gtag() calls become arrays). */
  payload: unknown
}

const MAX_ENTRIES = 300

let entries: readonly LogEntry[] = []
let seq = 0
const listeners = new Set<() => void>()

export function recordPush(payload: unknown, check: Check): void {
  entries = [...entries, { ...check, seq: ++seq, at: Date.now(), payload }].slice(-MAX_ENTRIES)
  listeners.forEach((l) => l())
}

export function clearLog(): void {
  entries = []
  listeners.forEach((l) => l())
}

export const getLog = (): readonly LogEntry[] => entries

export function subscribeLog(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
