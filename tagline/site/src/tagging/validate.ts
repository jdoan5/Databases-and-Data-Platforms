/**
 * The runtime validator, compiled once from the project-level contract.
 *
 * The schema lives outside site/ (tagline/tagging/) because it belongs to the whole
 * project: Stage 5's tag QA reads the same file. vite.config.ts allows the import.
 */
import schema from '../../../tagging/events.schema.json'
import { createContract, type Check, type Contract, type JsonSchema } from './contract'

let contract: Contract | null = null
let compileError = ''
try {
  contract = createContract(schema as JsonSchema)
} catch (err) {
  // A broken schema must not take the store down with it. Every push is reported
  // invalid instead, with the reason, so the Tag Inspector shows what went wrong.
  compileError = err instanceof Error ? err.message : String(err)
  console.error('[tagline] events.schema.json failed to compile:', err)
}

export function validateEntry(entry: unknown): Check {
  if (contract) return contract.check(entry)
  return { kind: 'unknown', label: 'unvalidated push', valid: false, errors: [`schema did not compile: ${compileError}`], rule: 'none' }
}
