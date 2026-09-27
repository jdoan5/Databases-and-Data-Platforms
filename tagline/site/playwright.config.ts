import { defineConfig } from '@playwright/test'

const PORT = 5173

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
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
