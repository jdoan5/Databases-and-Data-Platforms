import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { signIn, signOut } from '../auth/session'
import { useDocumentTitle, useSession } from '../hooks'

/**
 * Fake sign-in: an email field and nothing else. The email is hashed into a
 * pseudonymous user_id in the browser and then dropped; it is never stored or tagged.
 */
export function SignIn() {
  const session = useSession()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useDocumentTitle('Sign in')

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const submitter = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null
    const mode = submitter?.value === 'sign_up' ? 'sign_up' : 'login'
    const email = String(new FormData(form).get('email') ?? '')
    setBusy(true)
    setError('')
    try {
      await signIn(email, mode)
      form.reset()
    } catch {
      setError('Sign-in needs a secure context (https or localhost) for the browser’s crypto API.')
    } finally {
      setBusy(false)
    }
  }

  if (session) {
    return (
      <>
        <h1>Signed in</h1>
        <p>
          You are signed in. The only identifier this site keeps, and the one sent as <code>user_id</code>, is{' '}
          <code data-testid="user-id">{session.user_id}</code>, derived from your email by a one-way hash. The email
          itself was discarded.
        </p>
        <div className="button-row">
          <button type="button" onClick={signOut}>
            Sign out
          </button>
          <Link to="/">Back to the store</Link>
        </div>
      </>
    )
  }

  return (
    <>
      <h1>Sign in</h1>
      <p className="muted">
        No password and no account: this sign-in exists to show how a signed-in user is identified in the tags without
        sending the email anywhere.
      </p>
      <form className="signin" onSubmit={onSubmit}>
        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" autoComplete="email" required />
        <div className="signin-actions">
          <button type="submit" name="mode" value="login" className="primary" disabled={busy}>
            Sign in
          </button>
          <button type="submit" name="mode" value="sign_up" disabled={busy}>
            Create account
          </button>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </form>
    </>
  )
}
