/**
 * purchase fires exactly once per transaction_id.
 *
 * The confirmation page is where a duplicate purchase usually comes from: a reload,
 * a back-button return, a second tab, or React running an effect twice in
 * development. The sent ids live in localStorage, so all of those see the claim and
 * skip. The id is claimed before the event is pushed; a crash between the two loses
 * one purchase event rather than doubling one, which is the right side to fail on
 * for revenue.
 */
import { localStore, readJSON, writeJSON, type KeyValueStore } from '../lib/storage'

const KEY = 'tagline.purchases_sent'
/** Plenty for a demo store; stops the list growing without bound. */
const KEEP = 100

function sentIds(store: KeyValueStore): string[] {
  const stored = readJSON<unknown>(store, KEY, [])
  return Array.isArray(stored) ? stored.filter((s): s is string => typeof s === 'string') : []
}

/** Read-only: has purchase already been pushed for this id? */
export function isTransactionClaimed(transactionId: string, store: KeyValueStore = localStore()): boolean {
  return sentIds(store).includes(transactionId)
}

/** Returns true the first time it sees a transaction id, false every time after. */
export function claimTransaction(transactionId: string, store: KeyValueStore = localStore()): boolean {
  const sent = sentIds(store)
  if (sent.includes(transactionId)) return false
  writeJSON(store, KEY, [...sent, transactionId].slice(-KEEP))
  return true
}
