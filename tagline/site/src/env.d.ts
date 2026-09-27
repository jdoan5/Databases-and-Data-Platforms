interface ImportMetaEnv {
  /** GA4 measurement id (G-…). Unset by default: events stay in the dataLayer only. */
  readonly VITE_GA4_MEASUREMENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
