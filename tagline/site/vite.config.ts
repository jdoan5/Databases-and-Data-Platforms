import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// tagline/ — the project root. The event schema lives in tagline/tagging/, outside
// this Vite project, because Stage 5's tag QA reads the same file. Vite's dev server
// refuses to serve files outside the project unless they are allowed here.
const taglineRoot = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [taglineRoot] },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
    // A local .env with a real measurement id must not change what the tests push.
    env: { VITE_GA4_MEASUREMENT_ID: '' },
  },
})
