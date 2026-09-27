# Tagging plan: Tagline Supply (Stage 1)

| | |
|---|---|
| Contract version | 1.0.0 |
| Machine-readable contract | [`tagging/events.schema.json`](../tagging/events.schema.json) (JSON Schema draft 2020-12) |
| Implementation | `site/src/tagging/` (builders, dataLayer + gtag shim, validation, consent, purchase dedupe) |
| Checked against Google's docs | 2026-09-27 (sources at the end) |

This is the document product, analytics and engineering sign off before the site is tagged. It says
which events the store pushes to `window.dataLayer`, exactly when each one fires and when it must not,
what every parameter holds, and how a reviewer can check all of it without GTM Preview. The schema is
the same contract in a form a validator can run; if the two ever disagree, that is a bug in one of them.

Out of scope for Stage 1: a GTM container, BigQuery, hosting. Nothing is sent to Google unless a GA4
measurement id is configured (off by default).

---

## 1. Naming rules

| Rule | Why |
|---|---|
| Only GA4 recommended event names, exactly as Google spells them (`add_to_cart`, not `addToCart` or `add_to_basket`). No custom events in Stage 1. | GA4 builds its ecommerce reports from these names. Stage 2 unions with the GA4 sample dataset, which uses them. |
| Event and parameter names: snake_case, at most 40 characters. | GA4 collection limit. |
| String parameter values: at most 100 characters; `page_title` 300, `page_referrer` 420, `page_location` 1,000. | GA4 collection limits: parameters over them are not logged. |
| `item_list_id`: stable snake_case id (`all_products`, `category_drinkware`, `search_results`). `item_list_name`: the heading the user sees. | Ids group in reports; names can change. |
| Numbers are numbers (`19.99`), never strings (`"19.99"`). Money in USD with at most 2 decimals. | Google's reference types `value`, `price`, `quantity`, `tax`, `shipping` as numbers. |
| No personal data in any parameter: no email, name, username, phone or address, raw or URL-encoded. | Google's PII policy. See §7 and §9. |

## 2. Push format

Every event is one object pushed to the dataLayer. Ecommerce events carry their parameters inside an
`ecommerce` object (the GTM ecommerce format), and are preceded by a clearing push:

```js
dataLayer.push({ ecommerce: null });            // clear the previous ecommerce object
dataLayer.push({
  event: 'add_to_cart',
  ecommerce: {
    currency: 'USD',
    value: 41.97,                                // 3 × 13.99
    items: [{ item_id: 'TL-DRK-001', item_name: 'Ceramic Mug', item_brand: 'Tagline Supply',
              item_category: 'Drinkware', item_variant: 'White', price: 13.99, quantity: 3 }]
  }
});
dataLayer.push({ event: 'search', search_term: 'mug' });   // non-ecommerce: parameters at the top level
```

**Why `{ ecommerce: null }` first.** GTM merges every push into one data model, and when the old and
new values are both objects or both arrays it merges them recursively instead of replacing them
(Google's data-layer-helper documents this). Without the clear, an `add_to_cart` with one item pushed
after a `view_cart` with three reads back as three items: its own, plus two left over from the cart.
Setting `ecommerce` to `null` replaces the old object outright. Google's ecommerce guide shows the clear
before every ecommerce event; this plan makes it a rule: exactly one clear, immediately before each
ecommerce event, never before a non-ecommerce event.

Page code never builds these objects by hand. It calls a typed builder (`viewItem(product)`,
`purchase(order)` …) and `track()`, which pushes the clear and the event.

## 3. Events: when they fire

"Once per page view" means once per page visit: a new history entry, Back/Forward, or a replace that
changes the URL. A re-render, a state change or React StrictMode's second effect run is not a new page
view, and neither is a click on a link to the page already shown (the logo on home, the active
category, Cart on the cart page). react-router turns that click into a replace with a new
`location.key` but the same URL, so the site keys its page visits on the URL for replaces, not on the
key alone.

| # | Event | Fires when | Must NOT fire when |
|---|---|---|---|
| 1 | `page_view` | Every navigation, including the first load, after the route has set `document.title`. It is the first event of each page; that page's other events follow it. | A component re-renders; a link to the current URL is clicked; only state changes (filters that do not change the URL, the Tag Inspector opening). |
| 2 | `view_item_list` | A product list renders with at least one product: home (`all_products`), a category page (`category_<slug>`), search results (`search_results`). Once per list per page view. Items are every product shown, in display order. | The list is empty (a search with no results fires `search` but no `view_item_list`); the cart badge or any unrelated state changes. |
| 3 | `select_item` | A product in one of those lists is clicked, before navigating to the product. Same `item_list_id`/`item_list_name` as the list, one item with its `index`. | A product link outside a list is clicked (cart lines, confirmation page). |
| 4 | `view_item` | A product detail page renders for a product that exists. Once per page view. | The product id is unknown (the not-found page). |
| 5 | `add_to_cart` | The quantity of a product in the cart goes up (Add button, quantity + on the cart page). One event per action; `quantity` = units added by that action. | The quantity does not change. |
| 6 | `remove_from_cart` | The user lowers a quantity or removes a line. `quantity` = units removed by that action. | The cart is emptied because an order was placed (that is a purchase, not a removal). |
| 7 | `view_cart` | The cart page renders with at least one line. Once per page view. | The cart is empty; quantities change on the cart page (those are rows 5 and 6). |
| 8 | `begin_checkout` | The checkout page renders with a non-empty cart. Once per page view. | The cart is empty (the page shows a message instead); the user changes a select. |
| 9 | `add_shipping_info` | The user picks a shipping tier in the select (its change event). Again if they pick a different tier. | On render (the select starts on a disabled "Choose shipping" placeholder, so nothing is pre-chosen); re-picking the same tier. |
| 10 | `add_payment_info` | The user picks a payment type in the select. Again if they pick a different type. No payment details are ever asked for. | On render (disabled "Choose payment type" placeholder); re-picking the same type. |
| 11 | `purchase` | The order confirmation page renders for an order that exists, **once per `transaction_id`, ever**. The id is claimed in localStorage before the push, so a failure between the two loses one event rather than doubling one. | The confirmation page is reloaded, revisited in another tab, or reached by back/forward; React runs the effect twice in development; the order id is unknown. |
| 12 | `search` | The search form is submitted with a non-empty trimmed term. Fires even when there are no results. | On keystrokes; for an empty or whitespace term; when a results URL is opened directly or reloaded (no submit happened). |
| 13 | `login` | The fake sign-in form is submitted in "Sign in" mode. Pushed right after the `{ user_id }` push. | Sign-in is restored on app start (identity is re-pushed, but no new login happened). |
| 14 | `sign_up` | The fake sign-in form is submitted in "Create account" mode. Pushed right after the `{ user_id }` push. A sign-up does not also push `login`. | As row 13. |

Signing out pushes `{ user_id: null }` and no event (GA4 has no recommended logout event).

**Happy-path order** (the sequence the Stage 1 end-to-end test must assert; each ecommerce event is preceded by `{ ecommerce: null }`):

`page_view` (home) → `view_item_list` → `select_item` → `page_view` (product) → `view_item` → `add_to_cart` →
`page_view` (cart) → `view_cart` → `page_view` (checkout) → `begin_checkout` → `add_shipping_info` →
`add_payment_info` → `page_view` (confirmation) → `purchase`. Reloading the confirmation page adds a
`page_view` and no second `purchase`.

## 4. Parameters

### 4.1 Non-ecommerce events

| Event | Parameter | Type | Required | Example | Rule |
|---|---|---|---|---|---|
| `page_view` | `page_location` | string | yes | `http://localhost:5173/product/TL-DRK-001` | Full URL with protocol, no `#fragment`, ≤ 1,000 chars. |
| | `page_title` | string | yes | `Ceramic Mug, White · Tagline Supply` | `document.title` of the new route, ≤ 300 chars. |
| | `page_referrer` | string | when known | `http://localhost:5173/` | Previous in-app URL; on the first load, `document.referrer` if non-empty. Omitted, never `""`, when unknown. ≤ 420 chars. |
| `search` | `search_term` | string | yes | `ceramic mug` | Trimmed, inner whitespace collapsed, any email address replaced by `[email]`, cut to 100 chars. |
| `login`, `sign_up` | `method` | string | yes | `email` | Only `email` in Stage 1. |

### 4.2 Ecommerce events: what goes in `ecommerce`

R = required, · = not allowed. Every object is closed: a key not in this table fails validation.

| Event | `currency` | `value` | `item_list_id` / `item_list_name` | `items` | Extra |
|---|---|---|---|---|---|
| `view_item_list` | R | · | R | 1–200, each with `index` | |
| `select_item` | R | · | R | exactly 1, with `index` | |
| `view_item` | R | R | · | exactly 1 | |
| `add_to_cart` | R | R | · | exactly 1 | |
| `remove_from_cart` | R | R | · | exactly 1 | |
| `view_cart` | R | R | · | 1–200 | |
| `begin_checkout` | R | R | · | 1–200 | |
| `add_shipping_info` | R | R | · | 1–200 | `shipping_tier` R |
| `add_payment_info` | R | R | · | 1–200 | `payment_type` R |
| `purchase` | R | R | · | 1–200 | `transaction_id` R, `tax` R, `shipping` R |

| Parameter | Type | Allowed values / format | Example |
|---|---|---|---|
| `currency` | string | `USD` only (ISO 4217, upper case) | `USD` |
| `value` | number | > 0, see §6 | `65.97` |
| `item_list_id` | string | `^[a-z][a-z0-9_]{0,99}$` | `category_drinkware` |
| `item_list_name` | string | 1–100 chars | `Drinkware` |
| `shipping_tier` | string | `Ground`, `Express`, `Next Day` | `Ground` |
| `payment_type` | string | `Credit Card`, `PayPal`, `Gift Card` | `Credit Card` |
| `transaction_id` | string | `TL-<base36 time>-<base36 random>`; unique per order, never empty, no customer data | `TL-MG3K2ZQ1-0A7F3KD` |
| `tax` | number | ≥ 0 | `5.28` |
| `shipping` | number | ≥ 0 (a free tier sends 0) | `5` |

## 5. The `items[]` array

| Parameter | Type | Required | Example | Rule |
|---|---|---|---|---|
| `item_id` | string | yes | `TL-DRK-001` | Catalog SKU, `TL-<3 letters>-<3 digits>`. |
| `item_name` | string | yes | `Ceramic Mug` | Catalog name, ≤ 100 chars. |
| `item_brand` | string | yes | `Tagline Supply` | Always `Tagline Supply`. |
| `item_category` | string | yes | `Drinkware` | One of `Apparel`, `Drinkware`, `Bags`, `Office`, `Stickers`. |
| `item_variant` | string | if the product has one | `White` | Omitted, never `""`, when there is none. |
| `price` | number | yes | `13.99` | Unit price in USD, > 0. |
| `quantity` | integer | yes | `3` | ≥ 1. On add/remove: units added or removed by this action. In lists and on `view_item`: 1. |
| `index` | integer | in `view_item_list`, `select_item` | `0` | 0-based position in the list as displayed. |

No other item keys (Google allows `affiliation`, `coupon`, `discount`, `item_category2`–`5`,
`location_id` and up to 27 custom keys; Tagline sends none of them, so the schema rejects them).

## 6. Currency and value

- `currency: 'USD'` on every ecommerce event, including the list events that carry no `value`.
- **`value` = Σ `price` × `quantity` over the event's `items`**, summed in integer cents and converted
  back (5 × 19.99 = 99.95; plain floating point gives 99.94999999999999). One function computes it
  for every event.
- `value` never includes tax or shipping. On `purchase` they travel as `tax` and `shipping`; the amount
  paid is `value + tax + shipping` and is derivable, so it is not sent.
- Per event: `view_item` = price × 1; `add_to_cart` / `remove_from_cart` = price × units in this action;
  `view_cart`, `begin_checkout`, `add_shipping_info`, `add_payment_info`, `purchase` = cart subtotal.
- Worked example: 3 × Ceramic Mug at 13.99 + 1 × Classic Logo Tee at 24.00 → `value` 65.97; tax at the
  store's flat 8% → 5.28; Ground shipping → 5.00; paid 76.25.
- The schema checks that `value` is a positive number. It cannot check the arithmetic or the 2-decimal
  rule; unit tests do (§10).

## 7. Identity (`user_id`)

| Moment | dataLayer push | Rule |
|---|---|---|
| Visitor has never signed in | nothing | Google: don't send `user_id` at all. |
| Sign-in or sign-up submitted | `{ user_id: '<id>' }`, then `login` or `sign_up` | The id is in GTM's data model before the event. |
| App start while signed in | `{ user_id: '<id>' }` before the first `page_view` | So the first page view carries it. |
| Sign-out | `{ user_id: null }` | Google: `null`, never `""`, `" "` or the string `"null"`. |
| Signed in or out in another tab | `{ user_id: '<id>' }` or `{ user_id: null }`, no event | The session is shared by every tab; an open tab follows it so its later events carry the right id. |

- **Format**: 32 lowercase hex characters: an opaque account id, 128 random bits issued the first time
  an email signs in or up (`site/src/auth/accounts.ts`, standing in for an account service). The same
  email gets the same id again in this browser, which is what Stage 2 needs to stitch sessions; a real
  account service would return it on every device.
- **Never the email, and not derived from it.** The email only looks the account up, through a SHA-256
  kept in this browser's localStorage; it is never pushed, stored as text or logged, and the tagged id has
  no relation to it. The schema only accepts `^[0-9a-f]{32}$`, so a raw email, name or username cannot be
  sent. The pattern alone can't tell an opaque id from a hash of an email, so the id's origin is a code
  rule, covered by a unit test that a fresh browser gets a different id for the same email.
- **Meets Google's User-ID rule.** Google: "Your user ID must not contain information that a third party
  could use to determine a user's identity." A random id contains none.
- `user_id` is a standalone push, **never an event parameter** (Google: it is a configuration setting,
  not an event parameter or user property, and must not be registered as a custom dimension).

## 8. Consent (Consent Mode v2)

The site uses the standard shim `function gtag(){ dataLayer.push(arguments); }`.

| Moment | Push |
|---|---|
| App start, before anything else (dataLayer entry 0) | `gtag('consent', 'default', { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' })` |
| App start with a stored choice | `gtag('consent', 'update', { …all four… })` straight after the default, before the first `page_view` |
| Banner: Accept | `gtag('consent', 'update', …)` with all four `granted`; choice stored in localStorage |
| Banner: Reject | `gtag('consent', 'update', …)` with all four `denied` (explicit, so the choice is recorded); stored |
| Footer "Cookie settings" button | reopens the banner; the next choice pushes a new `update` |

- All four v2 types are set on every default and update. `ad_user_data` and `ad_personalization`
  (added to Consent Mode in November 2023) are set even though the store runs no ads, because Google
  requires them for v2 and it costs nothing.
- Events reach the dataLayer whatever the choice. Consent Mode tells Google's tags how to behave; it does
  not change what the site describes. With GA4 forwarding on, this is Google's "advanced" consent mode:
  gtag.js loads either way and sends cookieless pings while `analytics_storage` is denied.
- `user_id` is not gated by consent. Google sends `user_id` with cookieless pings while
  `analytics_storage` is denied, and its BigQuery export includes user IDs regardless of consent status.
  Accepted here because the id is pseudonymous and the store is a demo; a real store should decide with
  legal whether to push `user_id` only after analytics consent. **Needs sign-off.**
- Known simplification: one Accept/Reject pair, no per-purpose toggles. Google recommends offering each
  storage type separately; a real store with ads should.

## 9. Personal data

- The sign-in email never enters the dataLayer (unit-tested: no push contains it).
- Emails typed into the search box are replaced by `[email]` in `search_term`, and must not survive into
  `page_location`, `page_referrer` or `page_title` either. The schema rejects any of those four values
  that contains an email address, as `@` or URL-encoded `%40`.
- With GA4 forwarding on, the cleaned page fields are also passed to gtag.js with `set` before each
  `page_view` (§11). Otherwise gtag.js reads the page URL for its other hits from the address bar,
  which still holds an email that arrived in a shared link.
- `transaction_id` is random, not derived from the customer.

## 10. Validation and the Tag Inspector

**What runs.** Every push, including the ecommerce clear, identity pushes and gtag commands, is
validated against `events.schema.json` with Ajv (draft 2020-12 mode, `strict: true`) before it is
pushed. gtag's `arguments` object is copied to an array first. The schema's root accepts any push and
routes it: arrays → gtag command, objects with `event` → that event's `$defs` entry, other objects →
`{ ecommerce: null }` or `{ user_id }`. An invalid push logs `console.warn` with the errors and **is
still pushed**: the validator is a monitor, not a gate, because silently dropping data would hide the
bug it exists to show.

Pushes that do not come from the site's tagging module are checked too. At startup the site wraps
`dataLayer.push` once, so anything pushed another way (typed into the console, or by a third-party
script) is validated and listed in the Tag Inspector, marked "outside push". The wrapper calls the
push it replaced, so GTM or gtag.js wrapping it again later still works; the site's own pushes don't
rely on the wrapper at all.

**Tag Inspector.** Open the site with `?debug=1` (remembered in sessionStorage for the tab). A toggle
button opens a drawer listing every push, newest first: event name, time, ✓ valid or ✗ with the schema
errors, and the full JSON on expand. It is how a reviewer checks tags without GTM Preview or a GA4
property.

**Who checks what** (each ✓ is a Stage 1 requirement):

| Check | Schema (runtime) | Unit tests (Vitest) | End-to-end (Playwright) |
|---|---|---|---|
| Event name is in the contract | ✓ | | |
| Required parameters present, types right, no unknown keys | ✓ | ✓ every builder's output | ✓ every push in the funnel |
| Formats: USD, SKU pattern, list ids, user_id, URLs, enums | ✓ | | |
| No email in user_id, search, URLs, titles | ✓ | ✓ sign-in email in no push | |
| `value` = Σ price × quantity, tax and shipping excluded | | ✓ | ✓ purchase amounts |
| Money has at most 2 decimals (no `99.94999999999999`) | | ✓ catalog prices, value math | ✓ purchase amounts |
| `{ ecommerce: null }` before each ecommerce event | | ✓ `track()` | ✓ |
| Event order through the funnel | | | ✓ |
| `purchase` once per `transaction_id`, reload included | | ✓ dedupe | ✓ reload check |

Stage 5 grows the end-to-end column into full tag QA.

**Tolerated, not described.** Keys starting with `gtm.` on any event, and events named `gtm.*`, are
accepted: Google's own libraries write them into the dataLayer (the GTM snippet pushes
`{ event: 'gtm.js', 'gtm.start': … }`). gtag commands `js`, `config`, `set` and `event` appear only with
GA4 forwarding on; the schema checks their shape (`config` must carry `send_page_view: false`, `set`
carries either `user_id` or the page fields, and `event` params may not carry a `gtm.*` key).

## 11. Optional GA4 forwarding

Off unless `VITE_GA4_MEASUREMENT_ID` is set at build time; no id is committed.

- gtag.js loads, then `gtag('config', id, { send_page_view: false })`. The site sends its own
  `page_view` per route, so the automatic one must be off.
- In the GA4 web stream, turn off Enhanced measurement → Page views → "Page changes based on browser
  history events". Google documents that it sends `page_view` on history changes even with
  `send_page_view: false`, which would double-count every page.
- Each event is re-sent as `gtag('event', name, params)` with the `ecommerce` fields flattened into
  `params`, the shape gtag.js expects. The params are built from the event, not from the object in the
  dataLayer: once gtag.js loads it writes `gtm.uniqueEventId` into every pushed object, which would
  otherwise travel to GA4 as a junk `ep.gtm` parameter.
- Before each forwarded `page_view`, `gtag('set', { page_location, page_title, page_referrer })` with
  the same cleaned values. gtag.js takes the page URL for every other hit (its automatic
  `user_engagement`, the forwarded ecommerce events) from `document.location` unless told otherwise.
- `user_id`: `gtag('set', { user_id })` after sign-in, `gtag('set', { user_id: null })` on sign-out
  (Google's recommended form once the tag has loaded).
- In the GA4 web stream, also turn on data redaction for email. It is Google's best-effort second line
  of defence against an email in a URL, behind the site's own cleaning.
- GA4 also deduplicates purchases by `transaction_id` on web streams. That is a second line of defence,
  not a reason to skip the site's own dedupe: the raw dataLayer feed Stage 2 may collect has no such
  safety net.

## 12. How this data is used later

| Stage | Uses | Depends on |
|---|---|---|
| 2: stitch and enrich in BigQuery | Links anonymous sessions to a signed-in user by `user_id` (events earlier in the session belong to the user who signs in later, as GA4 itself treats them); unions site events with the GA4 Merchandise Store sample on the shared event names, `item_id`, `item_category` and USD values; revenue KPIs from `purchase`. | Stable pseudonymous `user_id`; GA4 event and item parameter names unchanged; `value` meaning the same thing on every event; `transaction_id` unique (SQL dedupes on it again). |
| 5: tag QA and KPI alerting | Reuses `events.schema.json` unchanged: the Playwright funnel collects every push and validates it against this file, then asserts order and the once-only rules in §3. KPI alerts watch funnel step counts and purchase value. | This file staying the single contract; the rules in §3 being precise enough to test. |

## 13. Deviations from Google

Where this plan is stricter than Google's reference, the site still sends valid GA4 data; the schema just
refuses things GA4 would quietly accept. The one place it falls short of Google is `user_id` (last row).

| Topic | Google (recommended events reference, ecommerce guide) | This plan | Why |
|---|---|---|---|
| `item_list_id`, `item_list_name` on list events | optional | required | Stage 2 list performance needs them. |
| `method` on `login` / `sign_up` | optional | required, `email` | The only method; a missing value is a bug. |
| `shipping_tier`, `payment_type` | optional | required, closed lists | The events mean nothing without them. |
| `tax`, `shipping` on `purchase` | optional | required, 0 allowed | Revenue splits in Stage 2. |
| Item identity | `item_id` **or** `item_name` | both, plus `item_brand`, `item_category`, `price`, `quantity` | Joins and category reports. |
| `quantity`, `index` | number; `quantity` defaults to 1 | integers, `quantity` always sent | Fractional units are a bug here. |
| `currency` on `select_item` | not in the parameter table (Google's own example sends it) | required | One rule for every ecommerce event. `view_item_list` lists it as required in both. |
| `value` on `view_item_list`, `select_item` | not defined | not allowed | A list has no single value. The project spec's general sentence ("ecommerce carries currency, value, items") would put it there; its event table does not, and Google does not. |
| Items per event | up to 200 on any event | exactly 1 on `select_item`, `view_item`, `add_to_cart`, `remove_from_cart` | One action, one product. |
| Custom parameters | up to 25 event and 27 item parameters | none | Closed objects catch typos (`transaction_Id`, `itemId`) and stray keys that GTM's merged model would carry into later events. |
| `customer_type`, `coupon`, `discount`, `affiliation`, `item_category2`–`5`, `location_id` | optional | not sent, rejected | No backend to know new vs returning; no coupons or stores. Adding one is a contract change (§14). |
| `page_view` | automatically collected, including history-based page views for SPAs; parameters from the page-view and SPA guides, not the recommended events reference | pushed by the site per page visit, `page_location` + `page_title` required, `page_referrer` when known | So each page's events have a `page_view` in front of them in the dataLayer. GA4's own history-based page views are turned off when forwarding (§11). |
| `user_id` | must not contain information a third party could use to determine a user's identity | an opaque random account id, issued per account (per browser in this demo) | Meets the rule. The gap is cross-device only: a real account service returns the same id everywhere (§7). |

The project spec and Google agree on everything else checked: event names, the `ecommerce` object, the
`{ ecommerce: null }` clear, `user_id` as a standalone push cleared with `null`, and the four Consent
Mode v2 parameters.

## 14. Changing the contract

Add or change an event or parameter in one pull request that touches all three: this plan,
`tagging/events.schema.json` (bump the version in its `$comment`), and the builder plus its unit test.
CI runs the unit tests against the schema, so a builder and the contract cannot drift silently.

## Sign-off

| Role | Name | Date |
|---|---|---|
| Product | | |
| Analytics | | |
| Engineering | | |

## Sources (checked 2026-09-27)

- GA4 recommended events reference (Tag Manager and gtag.js tabs): <https://developers.google.com/analytics/devguides/collection/ga4/reference/events>
- GA4 ecommerce guide: <https://developers.google.com/analytics/devguides/collection/ga4/ecommerce?client_type=gtm>
- Measure pageviews: <https://developers.google.com/analytics/devguides/collection/ga4/views>; single-page applications: <https://developers.google.com/analytics/devguides/collection/ga4/single-page-applications>; automatically collected events: <https://support.google.com/analytics/answer/9234069>
- Send user IDs: <https://developers.google.com/analytics/devguides/collection/ga4/user-id>; User-ID feature and limits: <https://support.google.com/analytics/answer/9213390>
- PII: <https://support.google.com/analytics/answer/6366371> and <https://support.google.com/analytics/answer/7686480>
- Event collection limits: <https://support.google.com/analytics/answer/9267744>
- Transaction ID deduplication: <https://support.google.com/analytics/answer/12313109>
- GTM data model merge rules: <https://github.com/google/data-layer-helper#the-abstract-data-model>
- Consent mode setup: <https://developers.google.com/tag-platform/security/guides/consent>; gtag `consent` reference: <https://developers.google.com/tag-platform/gtagjs/reference#consent>; consent mode overview: <https://support.google.com/analytics/answer/9976101>
