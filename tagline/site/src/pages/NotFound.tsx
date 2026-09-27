import { Link } from 'react-router'
import { useDocumentTitle } from '../hooks'

export function NotFound() {
  useDocumentTitle('Not found')
  return (
    <>
      <h1>Not found</h1>
      <p>
        There is nothing at this address. <Link to="/">Back to the store</Link>.
      </p>
    </>
  )
}
