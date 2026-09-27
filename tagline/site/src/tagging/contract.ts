/**
 * Validates dataLayer pushes against tagging/events.schema.json.
 *
 * Pure: it takes the parsed schema as an argument and touches no browser API, so the
 * site (at runtime), the unit tests and the Playwright test all use the same code.
 *
 * The schema's root accepts every kind of push and dispatches internally: arrays are
 * gtag() commands, objects with `event` are events, objects without are the ecommerce
 * clear or the user_id push. Validating against the root alone would be correct but
 * gives vague errors for gtag commands (the root uses oneOf there), so when the push
 * maps to a specific $defs entry this validates against that entry directly, which is
 * the same rule the root would reach. Anything that does not map falls back to the root.
 */
import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'

export type EntryKind = 'event' | 'ecommerce_clear' | 'user_id' | 'gtag' | 'unknown'

export interface Check {
  kind: EntryKind
  /** Event name, or a short description for non-event pushes. */
  label: string
  valid: boolean
  errors: string[]
  /** The schema location it was validated against, e.g. "$defs/add_to_cart". */
  rule: string
}

export type JsonSchema = Record<string, unknown>

export const isArgumentsObject = (x: unknown): x is IArguments =>
  Object.prototype.toString.call(x) === '[object Arguments]'

/**
 * JSON-safe copy of a push: gtag() Arguments become arrays and Dates ISO strings.
 * This is the form the schema describes and the form a test reads out of a browser.
 */
export function toPayload(entry: unknown): unknown {
  const value = isArgumentsObject(entry) ? Array.from(entry) : entry
  try {
    return JSON.parse(JSON.stringify(value)) as unknown
  } catch {
    return String(value)
  }
}

const isRecord = (x: unknown): x is Record<string, unknown> =>
  typeof x === 'object' && x !== null && !Array.isArray(x)

function formatError(e: ErrorObject): string {
  const where = e.instancePath || '(root)'
  switch (e.keyword) {
    case 'additionalProperties':
      return `${where}: unexpected property "${String(e.params.additionalProperty)}"`
    case 'required':
      return `${where}: missing "${String(e.params.missingProperty)}"`
    case 'const':
      return `${where}: must be ${JSON.stringify(e.params.allowedValue)}`
    case 'enum':
      return `${where}: must be one of ${(e.params.allowedValues as unknown[]).map((v) => JSON.stringify(v)).join(', ')}`
    case 'not':
      return `${where}: matches a forbidden pattern (looks like an email address?)`
    case 'if':
      // Ajv reports the branch that failed separately; this line adds nothing.
      return ''
    default:
      return `${where}: ${e.message ?? e.keyword}`
  }
}

export interface Contract {
  check(entry: unknown): Check
}

export interface ContractOptions {
  /** Ajv strict mode. On by default, so a schema Ajv only half understands fails loudly. */
  strict?: boolean
}

export function createContract(schema: JsonSchema, opts: ContractOptions = {}): Contract {
  const ajv = new Ajv2020({ allErrors: true, strict: opts.strict ?? true })
  const id = typeof schema.$id === 'string' && schema.$id ? schema.$id : 'urn:tagline:events'
  ajv.addSchema({ ...schema, $id: id })
  const defs = isRecord(schema.$defs) ? schema.$defs : {}
  const hasDef = (name: string) => Object.hasOwn(defs, name)

  const compiled = new Map<string, ValidateFunction>()
  const validatorFor = (pointer: string): ValidateFunction => {
    let v = compiled.get(pointer)
    if (!v) {
      v = pointer === '#' ? ajv.getSchema(id)! : ajv.compile({ $ref: `${id}${pointer}` })
      compiled.set(pointer, v)
    }
    return v
  }
  // The root reaches every $defs entry through its dispatch, so compiling it now makes
  // a broken schema fail at startup rather than on the first click that needs it.
  validatorFor('#')

  const eventEnum = isRecord(defs.event_name) && Array.isArray(defs.event_name.enum) ? defs.event_name.enum : null
  const isContractEvent = (name: string) => hasDef(name) && (eventEnum === null || eventEnum.includes(name))

  function run(kind: EntryKind, label: string, defName: string | null, data: unknown): Check {
    const pointer = defName && hasDef(defName) ? `#/$defs/${defName}` : '#'
    const v = validatorFor(pointer)
    const valid = v(data) as boolean
    const errors = valid ? [] : [...new Set((v.errors ?? []).map(formatError).filter(Boolean))]
    return { kind, label, valid, errors, rule: pointer === '#' ? 'schema root' : pointer.slice(2) }
  }

  function check(entry: unknown): Check {
    const data = toPayload(entry)

    if (Array.isArray(data)) {
      const [command, action] = data as unknown[]
      const detail = typeof action === 'string' && command !== 'js' ? action : null
      const label = ['gtag', String(command), detail].filter((p) => p !== null).join(' ')
      return run('gtag', label, typeof command === 'string' ? `gtag_${command}` : null, data)
    }

    if (isRecord(data) && typeof data.event === 'string') {
      return run('event', data.event, isContractEvent(data.event) ? data.event : null, data)
    }

    if (isRecord(data) && 'user_id' in data) return run('user_id', 'user_id', 'set_user_id', data)

    if (isRecord(data) && 'ecommerce' in data) return run('ecommerce_clear', 'ecommerce: null', 'ecommerce_clear', data)

    const label = isRecord(data) ? Object.keys(data).join(', ') || '{}' : typeof data
    return run('unknown', label, null, data)
  }

  return { check }
}
