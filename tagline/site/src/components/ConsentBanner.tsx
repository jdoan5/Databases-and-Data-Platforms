import { useConsentBannerVisible } from '../hooks'
import { setConsent } from '../tagging/consent'

export function ConsentBanner() {
  const visible = useConsentBannerVisible()
  if (!visible) return null
  return (
    <section className="consent" role="region" aria-labelledby="consent-title">
      <div>
        <h2 id="consent-title">Cookies</h2>
        <p>
          This demo store can use analytics and advertising cookies. Until you choose, Consent Mode tells Google tags
          that all storage is denied. Shopping events still appear in the page's dataLayer either way.
        </p>
      </div>
      <div className="consent-actions">
        <button type="button" onClick={() => setConsent('rejected')}>
          Reject
        </button>
        <button type="button" className="primary" onClick={() => setConsent('accepted')}>
          Accept
        </button>
      </div>
    </section>
  )
}
