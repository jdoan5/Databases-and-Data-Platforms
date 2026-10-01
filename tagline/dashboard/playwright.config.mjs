import { defineConfig } from '@playwright/test'

// The Google Chrome already installed on the machine (channel: 'chrome'), as the site's tests: no browser download.
// The server serves this folder, or DASHBOARD_ROOT (make dashboard-test-published points it at the portfolio copy),
// on a port of its own, and Playwright stops it when the run ends.
const PORT = 5190

export default defineConfig({
  testDir: 'tests',
  testMatch: /.*\.spec\.mjs/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop-1280', use: { viewport: { width: 1280, height: 900 } } },
    { name: 'mobile-375', use: { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } },
  ],
  webServer: {
    command: `node tests/serve.mjs ${PORT}`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
})
