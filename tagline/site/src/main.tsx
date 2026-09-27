import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { getSession } from './auth/session'
import './styles.css'
import { readDebugFlag } from './tagging/debug'
import { initTagging } from './tagging/init'

// Consent default, optional GA4, and user_id go into the dataLayer before React
// renders anything, so they sit in front of the first page_view.
initTagging({ userId: getSession()?.user_id ?? null })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App debug={readDebugFlag(window.location.search)} />
  </StrictMode>,
)
