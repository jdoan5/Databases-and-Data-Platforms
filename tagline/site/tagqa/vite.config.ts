import { defineConfig } from 'vite'
import base from '../vite.config.ts'

/**
 * The site's Vite config for the tag QA dev server, with the file watcher and HMR off.
 *
 * A file saved while the suite runs (or written a moment before it starts: macOS
 * delivers file events late) would otherwise make Vite reload every open page, and a
 * journey would see a page load it never asked for. With these off, the server serves
 * the code as it is when a page requests it, and a page never reloads on its own.
 */
export default defineConfig({
  ...base,
  server: { ...base.server, hmr: false, watch: null },
})
