import { defineConfig } from '@playwright/test'

/**
 * Not 5173: the test always starts its own dev server on a port of its own and stops
 * it afterwards, so it never runs against a dev server you left open (with a stale
 * cart in its tab, or a GA4 id in .env.local). --strictPort makes a busy port fail
 * loudly instead of quietly testing whatever else is listening.
 */
const PORT = 5180

/**
 * One browser, the Google Chrome already installed on the machine (channel: 'chrome'),
 * so no Playwright browser download. The test runs against the Vite dev server, which
 * also exercises the StrictMode double-effect guards.
 */
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
    // Environment variables beat .env files in Vite, so this keeps GA4 forwarding off
    // even when a local .env.local sets a measurement id.
    env: { VITE_GA4_MEASUREMENT_ID: '' },
  },
})
