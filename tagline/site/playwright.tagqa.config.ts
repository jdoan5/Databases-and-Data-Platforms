import { defineConfig } from '@playwright/test'
import { MEASUREMENT_ID, RESOLVER_RULES } from './tagqa/ga4'

/**
 * Tag QA (npm run tagqa): the Stage 1 funnel test, the dataLayer layer and the GA4 hit
 * layer, plus the rules' own self-test. docs/tag-qa.md describes it.
 *
 * Locally it drives the Google Chrome already installed (channel: 'chrome'), like the
 * funnel test; in CI (CI=true) Playwright's own Chromium, installed by the workflow.
 *
 * Two servers, on ports of their own so a dev server left open is never the one tested
 * (--strictPort fails loudly instead of testing whatever else is listening):
 *   5182  the Vite dev server, GA4 forwarding off (an environment variable beats
 *         .env.local), StrictMode on, no file watcher or HMR (tagqa/vite.config.ts):
 *         the dataLayer layer and the funnel test
 *   5183  a production build with the fake measurement id, served by vite preview:
 *         the GA4 hit layer. Built into node_modules/.tagqa/, never site/dist.
 */
const DEV = 5182
const GA4 = 5183
const GA4_BUILD = 'node_modules/.tagqa/ga4-build'
const CI = !!process.env.CI

export default defineConfig({
  testDir: '.',
  fullyParallel: true,
  workers: CI ? 2 : 3,
  retries: 0,
  forbidOnly: CI,
  reporter: CI
    ? [['list'], ['github'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    channel: CI ? undefined : 'chrome',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'rules', testMatch: 'tagqa/rules.spec.ts' },
    { name: 'funnel', testMatch: 'e2e/**/*.spec.ts', use: { baseURL: `http://localhost:${DEV}` } },
    { name: 'datalayer', testMatch: 'tagqa/datalayer.spec.ts', use: { baseURL: `http://localhost:${DEV}` } },
    {
      name: 'ga4',
      testMatch: 'tagqa/ga4.spec.ts',
      use: { baseURL: `http://localhost:${GA4}`, launchOptions: { args: [RESOLVER_RULES] } },
    },
  ],
  webServer: [
    {
      command: `npx vite --config tagqa/vite.config.ts --port ${DEV} --strictPort`,
      url: `http://localhost:${DEV}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_GA4_MEASUREMENT_ID: '' },
    },
    {
      command: `npx vite build --outDir ${GA4_BUILD} --emptyOutDir --logLevel warn && npx vite preview --outDir ${GA4_BUILD} --port ${GA4} --strictPort`,
      url: `http://localhost:${GA4}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_GA4_MEASUREMENT_ID: MEASUREMENT_ID },
    },
  ],
})
