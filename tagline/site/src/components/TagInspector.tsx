import { useEffect, useState, useSyncExternalStore } from 'react'
import { sessionStore } from '../lib/storage'
import { clearLog, getLog, subscribeLog, type LogEntry } from '../tagging/log'

const OPEN_KEY = 'tagline.inspector_open'
const EVENTS_ONLY_KEY = 'tagline.inspector_events_only'

function readFlag(key: string): boolean {
  try {
    return sessionStore().getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, on: boolean): void {
  try {
    if (on) sessionStore().setItem(key, '1')
    else sessionStore().removeItem(key)
  } catch {
    // not remembered; fine
  }
}

const time = (ms: number) => {
  const d = new Date(ms)
  const pad = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

function Entry({ entry }: { entry: LogEntry }) {
  return (
    <li className={`tag-entry ${entry.valid ? 'is-valid' : 'is-invalid'} kind-${entry.kind}`}>
      <div className="tag-entry-head">
        <span className="tag-status" aria-label={entry.valid ? 'valid' : 'invalid'}>
          {entry.valid ? '✓' : '✗'}
        </span>
        <span className="tag-name">{entry.label}</span>
        <time dateTime={new Date(entry.at).toISOString()}>{time(entry.at)}</time>
      </div>
      {entry.errors.length > 0 && (
        <ul className="tag-errors">
          {entry.errors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      )}
      <details>
        <summary>
          JSON <span className="tag-rule">checked against {entry.rule}</span>
        </summary>
        <pre>{JSON.stringify(entry.payload, null, 2)}</pre>
      </details>
    </li>
  )
}

/**
 * Lists every dataLayer push, newest first, with its validation result.
 * Shown when the site is opened with ?debug=1 (remembered for the browser session).
 */
export function TagInspector() {
  const entries = useSyncExternalStore(subscribeLog, getLog)
  const [open, setOpen] = useState(() => readFlag(OPEN_KEY))
  const [eventsOnly, setEventsOnly] = useState(() => readFlag(EVENTS_ONLY_KEY))

  useEffect(() => writeFlag(OPEN_KEY, open), [open])
  // On wide screens the page reflows beside the drawer instead of hiding under it.
  useEffect(() => {
    document.documentElement.classList.toggle('inspector-open', open)
    return () => document.documentElement.classList.remove('inspector-open')
  }, [open])
  useEffect(() => writeFlag(EVENTS_ONLY_KEY, eventsOnly), [eventsOnly])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const invalid = entries.filter((e) => !e.valid).length
  const shown = (eventsOnly ? entries.filter((e) => e.kind === 'event') : entries).toReversed()

  return (
    <>
      <button
        type="button"
        className={`inspector-toggle ${invalid ? 'has-invalid' : ''}`}
        aria-expanded={open}
        aria-controls="tag-inspector"
        onClick={() => setOpen((o) => !o)}
      >
        Tag Inspector <span className="pill">{entries.length}</span>
        {invalid > 0 && <span className="pill bad">{invalid} ✗</span>}
      </button>

      {open && (
        <aside id="tag-inspector" className="inspector" aria-label="Tag Inspector">
          <header className="inspector-head">
            <h2>Tag Inspector</h2>
            <p>
              {entries.length} pushes, {invalid === 0 ? 'all valid' : `${invalid} invalid`}. Newest first.
            </p>
            <div className="inspector-tools">
              <label>
                <input type="checkbox" checked={eventsOnly} onChange={(e) => setEventsOnly(e.target.checked)} /> Events
                only
              </label>
              <button type="button" onClick={clearLog}>
                Clear
              </button>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close Tag Inspector">
                Close
              </button>
            </div>
          </header>
          {shown.length === 0 ? (
            <p className="inspector-empty">Nothing pushed yet.</p>
          ) : (
            <ol className="tag-list">
              {shown.map((entry) => (
                <Entry key={entry.seq} entry={entry} />
              ))}
            </ol>
          )}
        </aside>
      )}
    </>
  )
}
