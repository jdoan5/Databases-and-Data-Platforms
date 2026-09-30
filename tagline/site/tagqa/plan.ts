/**
 * The tag test plan: every journey the tag QA suite drives, and exactly what each one
 * must push.
 *
 * Data only. `steps` are what a visitor does (journey.ts turns each into clicks and
 * key presses); `expect` is the exact sequence of dataLayer pushes, one list per page
 * load (the first `open`, then every `reload` or further `open` starts a new one).
 * In those lists an event is its name, a consent command is `consent default` or
 * `consent update granted|denied`, and the identity push is `user_id` or `user_id null`.
 * The `{ ecommerce: null }` clears are left out: the ecommerce-clear rule checks that
 * one sits immediately before every ecommerce event and nowhere else, and the golden
 * snapshot pins them too.
 *
 * Every journey also goes through the generic rules in rules.ts (contract, consent
 * first, clears, one page_view per page visit, purchase once per transaction_id, value
 * math, list consistency, no personal data, opaque user ids) and is compared with its golden snapshot
 * in golden/<id>.json. The GA4 layer (ga4.spec.ts) runs the same journeys against a
 * build with a fake measurement id and checks the hits gtag.js forms from them.
 *
 * Changing what the site pushes means changing this file, the golden (npm run
 * tagqa:update) and docs/tagging-plan.md in the same pull request.
 */

export type ConsentChoice = 'accept' | 'reject'
export type ShippingTier = 'Ground' | 'Express' | 'Next Day'
export type PaymentType = 'Credit Card' | 'PayPal' | 'Gift Card'

export type Step =
  /** page.goto: a new page load. The first step of every journey. */
  | { do: 'open'; path: string }
  /** The browser's reload: a new page load of the same URL. */
  | { do: 'reload' }
  /** The browser's Back / Forward buttons. */
  | { do: 'back' }
  | { do: 'forward' }
  /** Accept or Reject in the cookie banner. */
  | { do: 'consent'; choice: ConsentChoice }
  /** The footer's "Cookie settings" button, which reopens the banner. */
  | { do: 'cookieSettings' }
  /** The header logo (to home). */
  | { do: 'brand' }
  /** A link in the category bar. */
  | { do: 'category'; name: 'Apparel' | 'Drinkware' | 'Bags' | 'Office' | 'Stickers' }
  /** A product in the list on screen (home, category or search results). */
  | { do: 'selectItem'; itemId: string }
  /** On a product page: choose the quantity, then Add to cart. */
  | { do: 'addToCart'; quantity: number }
  /** The header's Cart link. */
  | { do: 'cart' }
  /** On the cart page: +, − and Remove on one line. */
  | { do: 'cartMore'; itemId: string }
  | { do: 'cartLess'; itemId: string }
  | { do: 'cartRemove'; itemId: string }
  /** The cart page's Checkout link. */
  | { do: 'checkout' }
  | { do: 'shipping'; tier: ShippingTier }
  | { do: 'payment'; type: PaymentType }
  | { do: 'placeOrder' }
  /** Type into the header search box and press Enter. */
  | { do: 'search'; term: string }
  /** The header's Sign in link. */
  | { do: 'signInLink' }
  /** On the sign-in page: an email, then "Sign in" (login) or "Create account" (sign_up). */
  | { do: 'signIn'; email: string; mode: 'login' | 'sign_up' }
  /** The header's Sign out button. */
  | { do: 'signOut' }
  /**
   * The browser's localStorage is cleared (the site's account directory with it,
   * src/auth/accounts.ts), as a second browser would start with none. No page load, no
   * push: what follows is the same email signing up somewhere new.
   */
  | { do: 'clearStorage' }

export interface Journey {
  /** Also the golden's file name: golden/<id>.json. */
  id: string
  title: string
  /** What regression this journey is there to catch. */
  guards: string
  steps: Step[]
  /** The exact push sequence, one list per page load (see the header comment). */
  expect: string[][]
  /**
   * Text typed into the site that must never reach a push or a hit, in any case or
   * encoding. Emails use example.com, a domain reserved for documentation.
   */
  typed?: string[]
  /**
   * How many distinct user ids the journey must push (the user-id-opaque rule). Signing
   * the same email up again after clearStorage must give a new id: the id is issued at
   * random, so one that comes back is derived from the email.
   */
  userIds?: number
}

/** The store's own rules the value-math rule checks against (tagging plan §6, site/src/checkout/orders.ts). */
export const STORE = {
  currency: 'USD',
  taxRate: 0.08,
  shipping: { Ground: 5, Express: 12, 'Next Day': 25 } as Record<ShippingTier, number>,
} as const

const SEARCH_EMAIL = 'qa.shopper@example.com'
const ACCOUNT_EMAIL = 'qa.member@example.com'
const SHARED_EMAIL = 'qa.friend@example.com'

export const JOURNEYS: Journey[] = [
  {
    id: 'purchase',
    title: 'Browse → product → cart → checkout → purchase',
    guards:
      'The happy path of the tagging plan (§3) in order, with consent accepted. The Cart link clicked on the cart page is not a new page, so it pushes nothing.',
    steps: [
      { do: 'open', path: '/' },
      { do: 'consent', choice: 'accept' },
      { do: 'selectItem', itemId: 'TL-DRK-001' },
      { do: 'addToCart', quantity: 2 },
      { do: 'cart' },
      { do: 'cart' },
      { do: 'checkout' },
      { do: 'shipping', tier: 'Express' },
      { do: 'payment', type: 'Credit Card' },
      { do: 'placeOrder' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item_list',
        'consent update granted',
        'select_item',
        'page_view',
        'view_item',
        'add_to_cart',
        'page_view',
        'view_cart',
        'page_view',
        'begin_checkout',
        'add_shipping_info',
        'add_payment_info',
        'page_view',
        'purchase',
      ],
    ],
  },
  {
    id: 'confirmation_revisit',
    title: 'Purchase, then Back, Forward and reload on the confirmation page',
    guards:
      'purchase once per transaction_id: returning to the confirmation page by Forward (same page load) or by a reload (a new one) pushes a page_view and never a second purchase. Consent rejected, and the rejection re-applied after the reload.',
    steps: [
      { do: 'open', path: '/product/TL-OFF-003' },
      { do: 'consent', choice: 'reject' },
      { do: 'addToCart', quantity: 1 },
      { do: 'cart' },
      { do: 'checkout' },
      { do: 'shipping', tier: 'Ground' },
      { do: 'payment', type: 'PayPal' },
      { do: 'placeOrder' },
      { do: 'back' },
      { do: 'forward' },
      { do: 'reload' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item',
        'consent update denied',
        'add_to_cart',
        'page_view',
        'view_cart',
        'page_view',
        'begin_checkout',
        'add_shipping_info',
        'add_payment_info',
        'page_view',
        'purchase',
        // Back: the cart page, now empty, so no view_cart.
        'page_view',
        // Forward: the confirmation page again, no purchase.
        'page_view',
      ],
      ['consent default', 'consent update denied', 'page_view'],
    ],
  },
  {
    id: 'search',
    title: 'Search: results, no results, an email typed into the box, a results URL opened directly',
    guards:
      'search fires on submit, before its page_view, even with no results; view_item_list only when there are results; an email typed into the box reaches no push (search_term, page_location and page_title carry [email]); opening a results URL is not a search.',
    steps: [
      { do: 'open', path: '/' },
      { do: 'search', term: 'mug' },
      { do: 'selectItem', itemId: 'TL-DRK-004' },
      { do: 'search', term: 'zzz' },
      { do: 'search', term: SEARCH_EMAIL },
      { do: 'open', path: '/search?q=tote' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item_list',
        'search',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'search',
        'page_view',
        'search',
        'page_view',
      ],
      ['consent default', 'page_view', 'view_item_list'],
    ],
    typed: [SEARCH_EMAIL],
  },
  {
    id: 'account',
    title: 'Sign-up, sign-out, login, reload while signed in, sign-out, the same email signed up in a second browser',
    guards:
      '{ user_id } before sign_up and login, { user_id: null } on sign-out and no event, the same id for the same email in the same browser, user_id re-pushed before the first page_view of a reload while signed in, the email in no push. The id is opaque: the same email signed up again after the browser storage is cleared (as in a second browser) gets a new id (userIds: 2), and no id is a hash of the email. In the hits: uid from the sign-up on, gone after the sign-out, the new id after the second sign-up. The banner is ignored, so no consent update at all.',
    steps: [
      { do: 'open', path: '/' },
      { do: 'signInLink' },
      { do: 'signIn', email: ACCOUNT_EMAIL, mode: 'sign_up' },
      { do: 'signOut' },
      { do: 'signIn', email: ACCOUNT_EMAIL, mode: 'login' },
      { do: 'reload' },
      { do: 'signOut' },
      { do: 'brand' },
      { do: 'clearStorage' },
      { do: 'signInLink' },
      { do: 'signIn', email: ACCOUNT_EMAIL, mode: 'sign_up' },
    ],
    expect: [
      ['consent default', 'page_view', 'view_item_list', 'page_view', 'user_id', 'sign_up', 'user_id null', 'user_id', 'login'],
      ['consent default', 'user_id', 'page_view', 'user_id null', 'page_view', 'view_item_list', 'page_view', 'user_id', 'sign_up'],
    ],
    typed: [ACCOUNT_EMAIL],
    userIds: 2,
  },
  {
    id: 'consent',
    title: 'Consent: accept, reload, reopen the banner and reject, reload',
    guards:
      'consent default (all denied) is the first push of every page load; a stored choice is re-applied as an update straight after it, before the first page_view; Accept and Reject each push one update.',
    steps: [
      { do: 'open', path: '/' },
      { do: 'consent', choice: 'accept' },
      { do: 'reload' },
      { do: 'cookieSettings' },
      { do: 'consent', choice: 'reject' },
      { do: 'category', name: 'Office' },
      { do: 'reload' },
    ],
    expect: [
      ['consent default', 'page_view', 'view_item_list', 'consent update granted'],
      [
        'consent default',
        'consent update granted',
        'page_view',
        'view_item_list',
        'consent update denied',
        'page_view',
        'view_item_list',
      ],
      ['consent default', 'consent update denied', 'page_view', 'view_item_list'],
    ],
  },
  {
    id: 'list_select',
    title: 'Category list → select_item, Back to the list, links to the page already shown',
    guards:
      'select_item carries the list id, name and position of the view_item_list it came from; Back to a list is a new page visit (page_view and view_item_list again); a link to the page already shown (the active category, the logo on home) is not.',
    steps: [
      { do: 'open', path: '/' },
      { do: 'category', name: 'Drinkware' },
      { do: 'category', name: 'Drinkware' },
      { do: 'selectItem', itemId: 'TL-DRK-002' },
      { do: 'back' },
      { do: 'selectItem', itemId: 'TL-DRK-004' },
      { do: 'brand' },
      { do: 'brand' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item_list',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'page_view',
        'view_item_list',
      ],
    ],
  },
  {
    id: 'cart_edit',
    title: 'Two products in the cart, then +, − and Remove',
    guards:
      'add_to_cart and remove_from_cart carry the units of that action, not the line total; view_cart has every line; value is summed in cents (5 × 19.99 = 99.95, not 99.94999999999999).',
    steps: [
      { do: 'open', path: '/category/bags' },
      { do: 'selectItem', itemId: 'TL-BAG-001' },
      { do: 'addToCart', quantity: 5 },
      { do: 'back' },
      { do: 'selectItem', itemId: 'TL-BAG-003' },
      { do: 'addToCart', quantity: 1 },
      { do: 'cart' },
      { do: 'cartMore', itemId: 'TL-BAG-001' },
      { do: 'cartLess', itemId: 'TL-BAG-003' },
      { do: 'cartRemove', itemId: 'TL-BAG-001' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'add_to_cart',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'add_to_cart',
        'page_view',
        'view_cart',
        'add_to_cart',
        'remove_from_cart',
        'remove_from_cart',
      ],
    ],
  },
  {
    id: 'two_orders',
    title: 'Two orders in one visit, each with its own transaction_id',
    guards:
      'Two purchases, one per order, with different transaction ids: GA4 counts one purchase per transaction_id, so an id that repeats loses the second order\'s revenue. A repeated id finds the second confirmation page already claimed (no second purchase: sequence), or without the claim check pushes it twice for one id (purchase-once); the golden pins <transaction_id:1> and <transaction_id:2>. Also the tiers and payment types no other journey uses (Next Day, Gift Card). The banner is ignored.',
    steps: [
      { do: 'open', path: '/product/TL-OFF-001' },
      { do: 'addToCart', quantity: 3 },
      { do: 'cart' },
      { do: 'checkout' },
      { do: 'shipping', tier: 'Next Day' },
      { do: 'payment', type: 'Gift Card' },
      { do: 'placeOrder' },
      { do: 'brand' },
      { do: 'selectItem', itemId: 'TL-APP-003' },
      { do: 'addToCart', quantity: 1 },
      { do: 'cart' },
      { do: 'checkout' },
      { do: 'shipping', tier: 'Ground' },
      { do: 'payment', type: 'Credit Card' },
      { do: 'placeOrder' },
    ],
    expect: [
      [
        'consent default',
        'page_view',
        'view_item',
        'add_to_cart',
        'page_view',
        'view_cart',
        'page_view',
        'begin_checkout',
        'add_shipping_info',
        'add_payment_info',
        'page_view',
        'purchase',
        'page_view',
        'view_item_list',
        'select_item',
        'page_view',
        'view_item',
        'add_to_cart',
        'page_view',
        'view_cart',
        'page_view',
        'begin_checkout',
        'add_shipping_info',
        'add_payment_info',
        'page_view',
        'purchase',
      ],
    ],
  },
  {
    id: 'shared_link',
    title: 'Landing from a shared link with an email in its query string',
    guards:
      'page_location carries [email] instead of the address in ?ref=, and the next page_referrer is that cleaned URL. In the hits, dl on every hit of the page (its page_view, view_item and add_to_cart) is the cleaned URL too: the site passes it with gtag(\'set\') before the page_view, since gtag.js would otherwise read the page URL, email included, from document.location.',
    steps: [
      { do: 'open', path: `/product/TL-DRK-001?ref=${encodeURIComponent(SHARED_EMAIL)}` },
      { do: 'addToCart', quantity: 1 },
      { do: 'cart' },
    ],
    expect: [['consent default', 'page_view', 'view_item', 'add_to_cart', 'page_view', 'view_cart']],
    typed: [SHARED_EMAIL],
  },
]
