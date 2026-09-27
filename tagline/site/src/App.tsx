import { BrowserRouter, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { Cart } from './pages/Cart'
import { Checkout } from './pages/Checkout'
import { Confirmation } from './pages/Confirmation'
import { Home } from './pages/Home'
import { NotFound } from './pages/NotFound'
import { ProductDetail } from './pages/ProductDetail'
import { SearchResults } from './pages/SearchResults'
import { SignIn } from './pages/SignIn'

export default function App({ debug }: { debug: boolean }) {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout debug={debug} />}>
          <Route index element={<Home />} />
          <Route path="category/:slug" element={<Home />} />
          <Route path="search" element={<SearchResults />} />
          <Route path="product/:itemId" element={<ProductDetail />} />
          <Route path="cart" element={<Cart />} />
          <Route path="checkout" element={<Checkout />} />
          <Route path="order/:transactionId" element={<Confirmation />} />
          <Route path="signin" element={<SignIn />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
