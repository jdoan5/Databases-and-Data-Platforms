# Tag QA: Tagline Stage 5 (the site)

| | |
|---|---|
| Run | `cd tagline/site && npm run tagqa` (46 tests, about 46 s, in the Google Chrome already installed) |
| Update the goldens | `npm run tagqa:update` |
| Code | [`site/tagqa/`](../site/tagqa/), [`site/playwright.tagqa.config.ts`](../site/playwright.tagqa.config.ts) |
| CI | [`.github/workflows/tagline-tagqa.yml`](../../.github/workflows/tagline-tagqa.yml) |
| The contract it enforces | [`tagging/events.schema.json`](../tagging/events.schema.json), [tagging plan](tagging-plan.md) |

Stage 1 ended with one Playwright test: the purchase funnel, every push checked against the contract.
Stage 5 grows it into a tag QA suite that runs before a change ships: a declarative test plan of nine
journeys, generic rules every journey must pass, golden snapshots of the whole dataLayer, and a second
layer that checks what gtag.js actually sends to GA4 for those pushes, without sending anything to
Google.

The job description this project was written against names three QA tools, Retina, KPI Shield and
Alert Goose. They look like one company's in-house tools; they are not public, and this project has
not used them. What this page describes is a public-tooling version of the first kind of check,
tags tested before release, built with Playwright, Ajv and the project's own JSON Schema.
[monitoring.md](monitoring.md) covers the other two: the collected data in BigQuery, and alerts on KPIs.

---

## Run it

```bash
cd tagline/site
npm ci
npm run tagqa                 # the whole suite: rules self-test, funnel, dataLayer layer, GA4 hit layer
npm run tagqa:update          # rewrite the golden snapshots (only when every rule passes)
npx playwright show-report    # the HTML report of the last run
npx playwright test -c playwright.tagqa.config.ts --project=datalayer -g purchase   # one layer, one journey
```

It needs Google Chrome installed (Playwright's `channel: 'chrome'`, as the Stage 1 test) and a network
connection: the GA4 layer downloads gtag.js from `www.googletagmanager.com` once per worker (up to three tries,
1 s and 2 s apart, so one network blip does not fail a journey), with the fake measurement id `G-TAGQA0000`. The suite starts its own two servers and stops them afterwards, on
ports of their own, so it never tests a dev server left open on 5173 or the build in `site/dist`:

| port | server | used by |
|---|---|---|
| 5182 | the Vite dev server, GA4 forwarding off, React StrictMode on (every effect runs twice), no file watcher or HMR ([`tagqa/vite.config.ts`](../site/tagqa/vite.config.ts)) | the funnel test, the dataLayer layer |
| 5183 | a production build with `VITE_GA4_MEASUREMENT_ID=G-TAGQA0000`, served by `vite preview`; built into `node_modules/.tagqa/` | the GA4 hit layer |

## What runs

| Playwright project | What it checks | Tests |
|---|---|--:|
| `rules` | The checks themselves, on hand-made recordings, no browser: a correct recording passes every rule; the same recording with one fault fails exactly the rule meant for it; the golden diff, the normaliser, the GA4 hit checks, the gtag.js retry; the plan covers all 14 contract events | 27 |
| `funnel` | The Stage 1 test ([`e2e/funnel.spec.ts`](../site/e2e/funnel.spec.ts)), unchanged | 1 |
| `datalayer` | Each journey on the dev server: every rule below, then its golden snapshot; with no measurement id, no request may leave the machine and no GA4 forwarding command may be pushed | 9 |
| `ga4` | The same journeys on the production build with GA4 forwarding on: every hit against the push it came from, every rule again (gtag commands included), and the site's pushes against the same golden | 9 |

Each journey's steps show up as Playwright test steps (`3. select TL-DRK-001 in the list`), so a trace or
the HTML report shows where a failure happened.

## The test plan

[`tagqa/plan.ts`](../site/tagqa/plan.ts) is data: for each journey, the steps a visitor takes, and the
exact sequence of pushes it must produce, one list per page load. In those lists an event is its name, a
consent command is `consent default` or `consent update granted|denied`, and the identity push is
`user_id` or `user_id null`; the `{ ecommerce: null }` clears have their own rule. The runner
([`tagqa/journey.ts`](../site/tagqa/journey.ts)) turns each step into clicks and key presses.

| Journey | Steps | Page loads, pushes, events | Guards |
|---|---|---|---|
| `purchase` | home, Accept, select a mug, add 2, cart, the Cart link again, checkout, Express, Credit Card, place order | 1, 25, 14 | The happy path of the tagging plan (§3) in order. The Cart link clicked on the cart page is not a new page. |
| `confirmation_revisit` | land on a product page, Reject, add, cart, checkout, Ground, PayPal, place order, Back, Forward, reload | 2, 25, 14 | purchase once per `transaction_id`: returning by Forward (same page load) or reload (a new one) pushes a `page_view` and never a second purchase. The rejection is re-applied after the reload. |
| `search` | search `mug`, select a result, search `zzz` (no results), search an email address, open `/search?q=tote` directly | 2, 21, 14 | `search` on submit, before its `page_view`, even with no results; `view_item_list` only with results; the typed email reaches no push (`search_term`, `page_location`, `page_title` carry `[email]`); opening a results URL is not a search. |
| `account` | Sign in link, create an account, sign out, sign in, reload, sign out, the logo, clear localStorage, Sign in link, create an account with the same email | 2, 20, 10 | `{ user_id }` before `sign_up` and `login`, `{ user_id: null }` on sign-out, the same id for the same email in the same browser, `user_id` re-pushed before the first `page_view` of a reload while signed in, the email in no push. The id is opaque: after the browser's storage is cleared (as in a second browser) the same email gets a new id (`userIds: 2`), and no id is a hash of the email. In the hits: `uid` from the sign-up on, gone after the sign-out, the new id after the second sign-up. The banner is ignored: no consent update at all. |
| `consent` | Accept, reload, Cookie settings, Reject, a category, reload | 3, 19, 8 | `consent default` (all denied) first on every page load; a stored choice re-applied straight after it, before the first `page_view`; one update per choice. In the hits: `gcs` follows each change. |
| `list_select` | the Drinkware category, the Drinkware link again, select the 2nd product, Back, select the 4th, the logo, the logo again | 1, 23, 14 | `select_item` carries the list id, name and position of the `view_item_list` it came from; Back to a list is a new page visit; a link to the page already shown is not. |
| `cart_edit` | 5 totes, Back, 1 laptop sleeve, cart, + on the totes, − on the sleeve, Remove the totes | 1, 30, 17 | `add_to_cart` and `remove_from_cart` carry the units of that action; `view_cart` has every line; money in cents (5 × 19.99 = 99.95; 6 × 19.99 = 119.94). |
| `two_orders` | land on a notebook, add 3, cart, checkout, Next Day, Gift Card, place order; the logo, select the hoodie, add, cart, checkout, Ground, Credit Card, place order | 1, 42, 25 | Two purchases with different `transaction_id`s: GA4 counts one purchase per id, so an id that repeats loses the second order's revenue. A repeated id finds the second confirmation page already claimed (no second purchase: `sequence`), or without the claim check pushes it twice for one id (`purchase-once`); the golden pins `<transaction_id:1>` and `<transaction_id:2>`. Also the tier and payment type no other journey uses. |
| `shared_link` | land on `/product/TL-DRK-001?ref=` plus an email address (URL-encoded), add to cart, cart | 1, 9, 5 | `page_location` carries `[email]` instead of the address, and the next `page_referrer` is that cleaned URL. In the hits, `dl` on every hit of the page is the cleaned URL too: the site passes it with `gtag('set')` before the `page_view`, and gtag.js would otherwise read the address from `document.location`. |

Together they push all 14 events in the contract; the `rules` project fails if a journey is removed and
an event is no longer covered.

**How pushes are recorded.** An init script creates `window.dataLayer` before the site's code runs, as a
GTM snippet would, and wraps its `push`, so every push is copied to Node the moment it happens, tagged
with its page load. That keeps the pushes from before a reload, which reading `window.dataLayer` at the
end cannot. The site's own wrapper then wraps this one, so the site behaves as it always does. After
each step the runner waits until every push of the current page load has arrived and nothing new has
come for 300 ms. A push later than that after a step is still recorded, by the next step's wait; after the last
step the runner waits for 1.5 s of quiet instead, so a late duplicate (a second `purchase` 700 ms on) is seen. That
bounds what an absence proves: a push more than 1.5 s after the last step is not seen by the dataLayer layer. The GA4
layer reads the recording again after its final wait for gtag.js, seconds later, so such a push goes through every
rule there. If the page ever replaced `window.dataLayer`, the runner fails rather than record less.

## The rules

[`tagqa/rules.ts`](../site/tagqa/rules.ts): pure functions of a recording, run on every journey in both
layers. Each finding names the page load, the push's position in the dataLayer and the push.

| Rule | Fails when |
|---|---|
| `contract` | A push does not validate against `events.schema.json` (Ajv, the site's own `contract.ts`, compiled from the file on disk). With GA4 forwarding on this includes the `gtag('config'\|'set'\|'event')` commands and gtag.js's `gtm.*` events. |
| `sequence` | The pushes of a page load differ from the plan's list; the message names the first difference and prints both sequences. |
| `consent-first` | The first push of a page load is not `consent default`, or a second default appears. |
| `ecommerce-clear` | An ecommerce event has no `{ ecommerce: null }` immediately before it, or a clear is not followed by an ecommerce event. |
| `page-view-once` | A second `page_view` for the same URL with no page change in between; a `page_view` whose `page_referrer` is not the previous `page_view`'s `page_location`. |
| `purchase-once` | More than one `purchase` for a `transaction_id`, over every page load of the journey. |
| `value-math` | `value` is not Σ `price` × `quantity` in cents; a money value has more than 2 decimals (`99.94999999999999`); a list or product view has a `quantity` other than 1, or list `index` values out of display order; `tax` is not 8% of `value`; `shipping` is not the price of the tier chosen in `add_shipping_info`. |
| `list-consistency` | `select_item` does not match the `view_item_list` it came from (list id, name, and the item at its `index`); `view_item` is for another product than the one selected; `purchase` has other items or value than the `begin_checkout` before it. |
| `no-pii` | Any string in any push (keys included) is email-shaped, raw or URL-encoded up to three times (the simulator's `findPii`, the check it runs on every hit), or contains the journey's typed text or its part before the @. |
| `user-id-opaque` | A `user_id` is, or is cut from, the MD5, SHA-1, SHA-256 or SHA-512 of the typed text (as typed, or trimmed and lowercased); or the journey pushes another number of distinct user ids than the plan's `userIds` (the account journey: 2). |

Every journey also fails on an uncaught page error, on the site's own validator warning about a push
(`site-validator`), and, in the dataLayer layer, on any request to anything but the local server
(`offsite`) and on any GA4 forwarding command (`gtag js`, `config`, `set`, `event`) or `gtm.*` event
(`forwarding-off`): that build has no measurement id, and the golden, written from the site's own pushes, leaves
those commands out, so only this rule would see them. Values in messages are printed as JSON, so a number that
became a string shows as `"27.98"` against `27.98`.

## Golden snapshots

[`tagqa/golden/<journey>.json`](../site/tagqa/golden/) holds every push of every page load, written from
the dev server with GA4 forwarding off. What changes from run to run is replaced by placeholders:
`<origin>` for the server, `<transaction_id:n>` and `<user_id:n>` for the n-th distinct id in the
journey (so the golden still shows that the login after a sign-out gets `<user_id:1>` again, the same
account, and the sign-up in a cleared browser `<user_id:2>`). Nine files, 214 pushes, 117 KB.

A run is compared push by push and parameter by parameter
([`tagqa/diff.ts`](../site/tagqa/diff.ts)). Pushes are aligned first (a longest common subsequence on the
push's kind, and the URL for a `page_view`), so one push too many shows as that one push rather than
every later push shifted by one:

```
✗ golden  tagqa/golden/list_select.json: the site's pushes differ from the golden:
    load 1 (golden 23 pushes, actual 25):
      + #7 page_view   unexpected (pushed, not in the golden)
            {"event":"page_view","page_location":"<origin>/category/drinkware","page_title":"Drinkware · Tagline Supply",…
      + #24 page_view   unexpected (pushed, not in the golden)
            {"event":"page_view","page_location":"<origin>/","page_title":"All products · Tagline Supply","page_referrer"…
    If the change is intended: npm run tagqa:update, review the golden diff, and commit it with the change.
```

(Mutation 7 below: a `page_view` for each click on a link to the page already shown.) A parameter that
changed shows as `~ #11 add_to_cart` with one line per parameter:
`ecommerce.items[0].item_brand: expected "Tagline Supply", got (missing)`. `#n` is the push's position in the
dataLayer; a push that is missing is numbered by its place in the golden (`- golden #2 { ecommerce: null }`). A push
that is missing in one place and pushed, unchanged, in another (a reorder) shows once: `↕ #15 user_id   moved
(golden #14)`.

A failed test writes `<journey>.golden.diff.txt`, the run's own snapshot `<journey>.actual.json` and
`tag-qa-violations.txt` into its folder under `test-results/`, and attaches the first two to the HTML
report.

**Updating on purpose.** `npm run tagqa:update` runs the dataLayer layer with `--update-snapshots=all`
and rewrites every golden that differs, but only for a journey whose every other rule passed: a push
that breaks the contract cannot be blessed into a golden. A journey with no golden yet gets one written
on a local run, and that run still fails, so the first snapshot is looked at before it is trusted. In CI
nothing is ever written. Change the site, `plan.ts` (if the sequence changed), the golden and the
[tagging plan](tagging-plan.md) in the same pull request, and read the golden's git diff as part of the
review.

The GA4 layer compares the site's own pushes (without the forwarding commands and `gtm.*` events) with
the same goldens. They match: turning GA4 forwarding on, and building for production, changes nothing
the dataLayer contract describes.

## The GA4 hit layer

[`tagqa/ga4.ts`](../site/tagqa/ga4.ts). The production build has a fake measurement id, so it loads
gtag.js and forwards every event (`site/src/tagging/ga4.ts`). Every request to a Google host goes
through one Playwright route: gtag.js itself is downloaded once per worker (up to three tries) and served from memory; each
`/g/collect` request is recorded, parsed (batched bodies too, with the simulator's parser,
`simulator/src/hits.js`) and answered with a 204 inside the browser, as Google's endpoint answers, so
gtag.js carries on as if delivered; anything else is aborted. The browser is also started with Google's
collection hosts made unresolvable, so a request the route never saw could not reach one either, and
the check fails if any Google request completes without the route answering it.

Before a reload and at the end of a journey, the runner waits until there is a hit for every event the
site pushed: gtag.js holds events and sends them in batches a few seconds apart, and in automated
Chrome it did not send the queue when the page unloaded (the simulator's finding). Those waits are most
of a GA4 journey's 5 to 12 seconds.

Each hit is held against the push it came from, page load by page load (gtag.js's page load id `_p`
groups them):

| Check | Fails when |
|---|---|
| `ga4-sequence` | Not exactly one hit per site event, with the same names in the same order (gtag.js's own `user_engagement` aside). |
| `ga4-params` | The hit's params are not the event's, translated the documented way: the `ecommerce` object flattened into the params; `page_location`, `page_title`, `page_referrer` as `dl`, `dt`, `dr`; `currency` as `cu`; `items` as `pr1…prN`; every other string as `ep.<name>` and number as `epn.<name>`, and no other `ep.*`/`epn.*`. Every hit's `dl` and `dt` must also be the cleaned `page_location` and `page_title` of the page it was sent from. |
| `ga4-user-id` | `uid` on a hit before any sign-in; a `uid` other than the id of the last `{ user_id }` push while signed in; a non-empty `uid` after a sign-out. |
| `ga4-consent` | `gcs` is not the state of the last consent command before the event (`G100` all denied, `G111` all granted). |
| `ga4-tid` | The hit is not for `G-TAGQA0000`. |
| `ga4-pii` | Any parameter of any request (query string and every body line), or the request as a whole, is email-shaped or carries the journey's typed text. |
| `ga4-leak`, `ga4-script` | A Google request completed without the route; gtag.js could not be downloaded in three tries. |

A run of the nine journeys records 32 `/g/collect` requests carrying 121 site hits, one per site event,
and one `user_engagement` of gtag.js's own. What gtag.js was seen doing, with this fake id and the
gtag.js served on 2026-09-30:

- **Consent.** Events pushed before the banner's Accept go out with `gcs=G100`, and are sent the moment
  the update lands; everything after with `G111`. After Reject, `G100` again.
- **`uid` after a sign-out.** After `gtag('set', { user_id: null })`, gtag.js keeps sending the
  parameter, empty (`uid=`), rather than dropping it. The check accepts an empty `uid` after a sign-out
  as no user id, and still requires it to be absent before any sign-in. Whether GA4 stores an empty
  `uid` as a NULL `user_id` is not verified here, since nothing is sent to Google; the site's export
  in Stage 2 has no sign-outs to look at.
- **`dl` on every hit** is the cleaned page URL the site passed with `gtag('set', …)` before the page's
  `page_view`; a `search` hit carries the URL of the page the search was typed on.
- **No enhanced-measurement events.** With the fake id, gtag.js sends no history-based `page_view`s, no
  `scroll`, no `view_search_results`: a real property's stream settings decide those, so this layer
  cannot vouch for them. The tagging plan (§11) says which to turn off.

## CI

[`tagline-tagqa.yml`](../../.github/workflows/tagline-tagqa.yml) runs `npm run tagqa` on every push or pull
request that touches `tagline/site/`, `tagline/tagging/`, the simulator's hit parser or the workflow:
`npm ci`, `npx playwright install --with-deps chromium`, then the suite on Playwright's Chromium
(`CI=true` switches the browser, 2 workers, the GitHub reporter, and no golden writing). It uploads the
Playwright HTML report on every run that is not cancelled, and on failure `test-results/` (per failed
test: the violations, the golden diff, the actual snapshot, the trace). Permissions: `contents: read`.

[`tagline-site.yml`](../../.github/workflows/tagline-site.yml) (typecheck, unit tests, build) keeps its
jobs as they were, and its typecheck now covers `tagqa/`; since `tagqa/` imports the simulator's hit
parser, that file joined its path filter. It needs no browser, and a gtag.js download that fails does
not hold up its answer. The funnel test, local-only until now, runs in
the new workflow.

Verified since: the push of 2026-09-30 (16:30 UTC) ran the workflow on GitHub's runners, and the suite passed 46 of
46 on Playwright's Chromium 153 (Chrome for Testing), 2 workers, in 1.2 min; `tagline-site` and `tagline-pipeline`
passed on the same push. What follows was written before that run.

Not verified from here at the time: a run on GitHub's runners, and with it the browser CI uses. With `CI` set the config
leaves `channel` unset, so Playwright launches its own Chromium (the headless shell), which is not installed on
this machine: a first version of this page said the suite had passed locally with `CI=1`, which that config
cannot have done. What was run instead, after review: the committed config with `CI=1` and one change, `channel:
'chrome'`, in a temporary config file deleted afterwards (2 workers, `forbidOnly`, the GitHub reporter, no golden
writing): 46 of 46 in 1.0 min. Everything the GA4 layer relies on in gtag.js (batching, no flush on unload, the
empty `uid=` after a sign-out, how `dl` is derived) has been seen in branded Chrome only; the Chromium path is
unverified until the workflow's first run. The workflow file was checked with [actionlint](https://github.com/rhysd/actionlint) 1.7.12 (the
author's container image, no network), with no findings, and every action it uses exists at the pinned
major version (`actions/checkout@v7`, `actions/setup-node@v7`, `actions/upload-artifact@v7`; the first two
already run in `tagline-site.yml`, and `upload-artifact@v7` takes the `name`, `path` and `retention-days`
inputs used here).

## Does it catch regressions?

Twelve mutations, each made in the real site code, one at a time, the whole suite run against it, and
the file then restored byte for byte from a copy (`git diff -- tagline/site/src` empty after each).
The first five are the regressions Stage 5 set out to catch; the sixth breaks only what GA4 receives;
the seventh is the single-page-app mistake Stage 1's page-visit id exists to avoid. Mutations 8 to 12
are the ones a review found passing the first version of the suite (36 tests; "before" below): a
transaction id that never changes, a `user_id` computed from the email, forwarding without the
`gtag('set')` that keeps an email in a shared link out of `dl`, a duplicate purchase pushed late, and
forwarding commands with GA4 off. They led to the `two_orders` and `shared_link` journeys, the second
sign-up in the `account` journey, the `user-id-opaque` and `forwarding-off` rules and the 1.5 s final
wait. All twelve were then run against the final suite (46 tests, 2026-09-30); the table is that run.

| # | Mutation (file in `site/src/`) | Tests failed, of 46 | Rules that fired | Stage 1 funnel test alone | Before (of 36) |
|---|---|---|---|---|---|
| 1 | `add_to_cart` without `item_brand` (`tagging/events.ts`) | 11: `purchase`, `confirmation_revisit`, `cart_edit`, `two_orders`, `shared_link` in both layers, the funnel | `contract`, `site-validator`, `golden` | caught | 7 |
| 2 | A second `page_view` hook in the layout, keyed on the path, so every route change pushes two (`components/Layout.tsx`) | 19: every journey in both layers, the funnel | `sequence`, `page-view-once`, `golden`; in the GA4 layer also `ga4-params` | caught | 15 |
| 3 | `purchase` pushed without checking the claim, so a revisit pushes it again (`pages/Confirmation.tsx`) | 3: `confirmation_revisit` in both layers, the funnel | `sequence`, `purchase-once`, `golden` | caught (the reload) | 3 |
| 4 | The search box's raw text as `search_term`, an email included (`components/Layout.tsx`) | 2: `search` in both layers | `contract`, `no-pii`, `site-validator`, `golden`; in the GA4 layer also `ga4-pii` | not caught (it types no search) | 2 |
| 5 | `track()` without the `{ ecommerce: null }` clear (`tagging/track.ts`) | 19: every journey in both layers, the funnel | `ecommerce-clear`, `golden` | caught | 15 |
| 6 | GA4 forwarding sends `ecommerce` as one param instead of flattening it (`tagging/ga4.ts`) | 9: every journey, GA4 layer only | `ga4-params` | not caught | 7 |
| 7 | `page_view` keyed on the router's `location.key` instead of the page visit (`components/Layout.tsx`) | 5: `purchase` and `list_select` in both layers, the funnel | `sequence`, `page-view-once`, `golden` | caught (the Cart click) | 5 |
| 8 | Every order gets the same `transaction_id`, `TL-ORDER-1` (`checkout/orders.ts`) | 2: `two_orders` in both layers | `sequence`, `golden` | not caught | 0 |
| 9 | `user_id` = the first 32 hex characters of the SHA-256 of the email (`auth/accounts.ts`) | 2: `account` in both layers | `user-id-opaque`, `golden` | not caught | 0 |
| 10 | No `gtag('set', …)` before a forwarded `page_view` (`tagging/ga4.ts`) | 9: every journey, GA4 layer only | `ga4-pii` (`shared_link`), `ga4-params` | not caught | the 7 GA4 journeys, by `ga4-params` alone, through a local-port artifact (below) |
| 11 | A second `purchase` 700 ms after the first (`pages/Confirmation.tsx`) | 6: `purchase`, `confirmation_revisit`, `two_orders` in both layers | `sequence`, `purchase-once`, `golden` | not caught | 3, not the dataLayer layer's `purchase` |
| 12 | Forwarding with GA4 off: no `if (!ga4MeasurementId) return` (`tagging/ga4.ts`) | 10: every journey in the dataLayer layer, the funnel | `forwarding-off` | caught, by accident (its Tag Inspector check reads `gtag event purchase`) | 1, the funnel |

Every mutation failed the suite with a message naming the rule, the push and what was wrong; the
`rules` project (27 tests) passed each time, as it should, since it tests the checks and not the site.
What the runs showed beyond "it fails":

- **3.** The first visit still pushes one purchase: React StrictMode's second effect run is stopped by
  the page's own ref guard, so only a revisit shows the missing claim check. Forward and the reload each
  pushed one more: three purchases for one `transaction_id`.
- **4.** The dataLayer (`contract`, `no-pii`) and the hit that reached the stubbed endpoint (`ga4-pii`,
  on `ep.search_term`) both fail. The funnel test never searches, so on its own it would have let this
  through.
- **6.** The dataLayer is untouched and valid: the contract's `gtag_event` allows any parameter name but
  `gtm.*`, so the funnel, the dataLayer layer and every dataLayer rule pass. Only the hits show it:
  gtag.js sends the nested object as `ep.ecommerce=[object Object]`, with no items, currency or value.
  This is the case the GA4 layer exists for.
- **2**, in the GA4 layer: the extra `page_view`s, which have no `page_referrer`, went out with the `dr`
  of the `page_view` before them: gtag.js keeps a referrer passed with `gtag('set', …)` until the next one. The
  site's real `page_view`s always carry a referrer after the first of a page load, so this only shows
  under the mutation, but it is the kind of difference only the hit layer can see.
- **8.** GA4 counts one purchase per `transaction_id`, so a fixed id would keep the first order's revenue and drop
  every later one. Before, no journey placed two orders, and the normaliser turns any id into `<transaction_id:1>`,
  so nothing could see it. Now the second order's confirmation page finds its id already claimed and pushes no
  purchase; the golden diff shows why: the second `/order/` URL carries `<transaction_id:1>` where the golden has
  `<transaction_id:2>`.
- **9.** Before, only "the same id for the same email" was tested, which an id computed from the email also passes.
  Now the rule fires on every `user_id` push (it is the SHA-256 of the typed address) and on the count: after the
  storage is cleared the same email still gets the same id.
- **10.** gtag.js reads `document.location` when no `page_location` was set. On the local servers it drops the port,
  so before the review every GA4 journey failed only on `dl: expected "http://localhost:5183/", got
  "http://localhost/"`, which a production host without a port would not show. `shared_link` fails for the real
  reason, whatever the host: `ga4-pii` on the address in `dl`.
- **11.** Before, the 300 ms quiet window after the last step ended the recording before the late purchase; now the
  last step waits for 1.5 s of quiet, and the GA4 layer reads the recording again after its last wait for gtag.js.
- **12.** The golden is written from the site's own pushes, which leave the forwarding commands out, so the dataLayer
  layer's goldens and rules passed; `forwarding-off` now fails on the first `gtag set`.

The failure output of each run, from the list reporter (dataLayer layer unless noted; long runs of
similar lines cut where marked `…`):

**1. `add_to_cart` without `item_brand`**

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 3 rule(s): contract, site-validator, golden
  ✗ contract        load 1 #11 add_to_cart: does not match $defs/add_to_cart: /ecommerce/items/0: missing "item_brand"
  ✗ site-validator  http://localhost:5182/product/TL-DRK-001: [tagline] dataLayer push "add_to_cart" does not match the contract: [/ecommerce/items/0: missing "item_brand"] {event: add_to_cart, ecommerce: Object}
  ✗ golden          tagqa/golden/purchase.json: the site's pushes differ from the golden:
      load 1 (golden 25 pushes, actual 25):
        ~ #11 add_to_cart
              ecommerce.items[0].item_brand: expected "Tagline Supply", got (missing)
      If the change is intended: npm run tagqa:update, review the golden diff, and commit it with the change.
```

**2. Two `page_view`s per route change**

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 3 rule(s): sequence, page-view-once, golden
  ✗ sequence        load 1: step 4 of the sequence: expected "consent update granted", got "page_view"
      expected: consent default → page_view → view_item_list → consent update granted → select_item → page_view → view_item → add_to_cart → page_view → view_cart → page_view → begin_checkout → add_shipping_info → add_payment_info → page_view → purchase
      actual:   consent default → page_view → view_item_list → page_view → page_view → consent update granted → select_item → page_view → view_item → page_view → add_to_cart → page_view → view_cart → page_view → page_view → begin_checkout → page_view → add_shipping_info → add_payment_info → page_view → purchase → page_view
  ✗ page-view-once  load 1 #4 page_view: a second page_view for http://localhost:5182/ with no page change in between (the first is #1)
  ✗ page-view-once  load 1 #5 page_view: a second page_view for http://localhost:5182/ with no page change in between (the first is #4)
  ✗ page-view-once  load 1 #12 page_view: a second page_view for http://localhost:5182/product/TL-DRK-001 with no page change in between (the first is #9)
  …
  ✗ golden          tagqa/golden/purchase.json: the site's pushes differ from the golden:
      load 1 (golden 25 pushes, actual 31):
        + #4 page_view   unexpected (pushed, not in the golden)
              {"event":"page_view","page_location":"<origin>/","page_title":"All products · Tagline Supply"}
        …
```

In the GA4 layer, the same run also reported:

```
  ✗ ga4-params      load 1 hit 7 page_view (dataLayer #24): dr: expected "", got "http://localhost:5183/"
  ✗ ga4-params      load 1 hit 11 page_view (dataLayer #36): dr: expected "", got "http://localhost:5183/product/TL-DRK-001"
```

**3. `purchase` again on a revisit**

```
Error: tag QA: journey "confirmation_revisit" (dataLayer layer, dev server) broke 3 rule(s): sequence, purchase-once, golden
  ✗ sequence       load 1: step 16 of the sequence: expected nothing more, got "purchase"
      expected: consent default → page_view → view_item → consent update denied → add_to_cart → page_view → view_cart → page_view → begin_checkout → add_shipping_info → add_payment_info → page_view → purchase → page_view → page_view
      actual:   consent default → page_view → view_item → consent update denied → add_to_cart → page_view → view_cart → page_view → begin_checkout → add_shipping_info → add_payment_info → page_view → purchase → page_view → page_view → purchase
  ✗ sequence       load 2: step 4 of the sequence: expected nothing more, got "purchase"
      expected: consent default → consent update denied → page_view
      actual:   consent default → consent update denied → page_view → purchase
  ✗ purchase-once  journey: purchase pushed 3 times for transaction_id TL-MUO6A00N-0PFR5UE (load 1 #19, load 1 #23, load 2 #4)
  ✗ golden         tagqa/golden/confirmation_revisit.json: the site's pushes differ from the golden:
      load 1 (golden 22 pushes, actual 24):
        + #22 { ecommerce: null }   unexpected (pushed, not in the golden)
              {"ecommerce":null}
        + #23 purchase   unexpected (pushed, not in the golden)
              {"event":"purchase","ecommerce":{"transaction_id":"<transaction_id:1>","currency":"USD","value":26,"tax":2.08…
      load 2 (golden 3 pushes, actual 5):
        …
```

**4. The typed email in `search_term`** (GA4 layer)

```
Error: tag QA: journey "search" (GA4 hit layer, production build) broke 5 rule(s): ga4-pii, contract, no-pii, site-validator, golden
  ✗ ga4-pii         request 2: ep.search_term "qa.shopper@example.com": email-shaped; the text typed into the site
  ✗ contract        load 1 #33 search: does not match $defs/search: /search_term: matches a forbidden pattern (looks like an email address?)
  ✗ no-pii          load 1 #33 search: search_term "qa.shopper@example.com": email-shaped; the text typed into the site
  ✗ no-pii          load 1 #34 gtag event search: [2].search_term "qa.shopper@example.com": email-shaped; the text typed into the site
  ✗ site-validator  http://localhost:5183/search?q=zzz: [tagline] dataLayer push "search" does not match the contract: [/search_term: matches a forbidden pattern (looks like an email address?)] {event: search, search_term: qa.shopper@example.com}
  ✗ golden          tagqa/golden/search.json: the site's pushes (with GA4 forwarding on, in the production build) differ from the golden:
      load 1 (golden 17 pushes, actual 17):
        ~ #15 search
              search_term: expected "[email]", got "qa.shopper@example.com"
```

**5. No `{ ecommerce: null }`** (the final suite's run; a missing push is numbered by its place in the golden)

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 2 rule(s): ecommerce-clear, golden
  ✗ ecommerce-clear  load 1 #2 view_item_list: no { ecommerce: null } immediately before it (the push before is page_view)
  ✗ ecommerce-clear  load 1 #4 select_item: no { ecommerce: null } immediately before it (the push before is consent update granted)
  ✗ ecommerce-clear  load 1 #6 view_item: no { ecommerce: null } immediately before it (the push before is page_view)
  ✗ ecommerce-clear  load 1 #7 add_to_cart: no { ecommerce: null } immediately before it (the push before is view_item)
  …
  ✗ golden           tagqa/golden/purchase.json: the site's pushes differ from the golden:
      load 1 (golden 25 pushes, actual 16):
        - golden #2 { ecommerce: null }   missing (in the golden, not pushed)
              {"ecommerce":null}
        - golden #5 { ecommerce: null }   missing (in the golden, not pushed)
              {"ecommerce":null}
        …
```

**6. GA4 forwarding not flattened** (GA4 layer; the only layer that failed)

```
Error: tag QA: journey "purchase" (GA4 hit layer, production build) broke 1 rule(s): ga4-params
  ✗ ga4-params  load 1 hit 2 view_item_list (dataLayer #7): 5 differences
        ep.item_list_id: expected "all_products", got (missing)
        ep.item_list_name: expected "All products", got (missing)
        ep.ecommerce: unexpected "[object Object]"
        items: expected 20 entries, got 0
        cu: expected "USD", got (missing)
  ✗ ga4-params  load 1 hit 5 view_item (dataLayer #19): 4 differences
        ep.ecommerce: unexpected "[object Object]"
        epn.value: expected 13.99, got (missing)
        items[0]: expected {"item_id":"TL-DRK-001","item_name":"Ceramic Mug","item_brand":"Tagline Supply","item_category":"Drinkware","…, got (missing)
        cu: expected "USD", got (missing)
  …
```

**7. `page_view` on a click to the page already shown**

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 3 rule(s): sequence, page-view-once, golden
  ✗ sequence        load 1: step 12 of the sequence: expected "begin_checkout", got "page_view"
      expected: consent default → page_view → view_item_list → consent update granted → select_item → page_view → view_item → add_to_cart → page_view → view_cart → page_view → begin_checkout → add_shipping_info → add_payment_info → page_view → purchase
      actual:   consent default → page_view → view_item_list → consent update granted → select_item → page_view → view_item → add_to_cart → page_view → view_cart → page_view → page_view → begin_checkout → add_shipping_info → add_payment_info → page_view → purchase
  ✗ page-view-once  load 1 #15 page_view: a second page_view for http://localhost:5182/cart with no page change in between (the first is #12)
  ✗ golden          tagqa/golden/purchase.json: the site's pushes differ from the golden:
      load 1 (golden 25 pushes, actual 26):
        + #15 page_view   unexpected (pushed, not in the golden)
              {"event":"page_view","page_location":"<origin>/cart","page_title":"Cart · Tagline Supply","page_referrer":"<o…
```

**8. The same `transaction_id` for every order**

```
Error: tag QA: journey "two_orders" (dataLayer layer, dev server) broke 2 rule(s): sequence, golden
  ✗ sequence  load 1: step 26 of the sequence: expected "purchase", got nothing more
      expected: consent default → page_view → view_item → add_to_cart → … → add_payment_info → page_view → purchase
      actual:   consent default → page_view → view_item → add_to_cart → … → add_payment_info → page_view
  ✗ golden    tagqa/golden/two_orders.json: the site's pushes differ from the golden:
      load 1 (golden 42 pushes, actual 40):
        ~ #39 page_view
              page_location: expected "<origin>/order/<transaction_id:2>", got "<origin>/order/<transaction_id:1>"
        - golden #40 { ecommerce: null }   missing (in the golden, not pushed)
              {"ecommerce":null}
        - golden #41 purchase   missing (in the golden, not pushed)
              {"event":"purchase","ecommerce":{"transaction_id":"<transaction_id:2>","currency":"USD","value":58,"tax":4.64…
```

**9. A `user_id` computed from the email**

```
Error: tag QA: journey "account" (dataLayer layer, dev server) broke 2 rule(s): user-id-opaque, golden
  ✗ user-id-opaque  load 1 #5 user_id: user_id "ffdd8223a10aadd0f28159d08079a49d" is cut from the SHA256 of the typed text (as typed): derived from the email, so not opaque
  …
  ✗ user-id-opaque  journey: 1 distinct user_id(s) pushed, the plan expects 2: the same email signed up again with the browser's storage cleared must get a new id (one that comes back is computed from the email)
  ✗ golden          tagqa/golden/account.json: the site's pushes differ from the golden:
      load 2 (golden 10 pushes, actual 10):
        ~ #8 user_id
              user_id: expected "<user_id:2>", got "<user_id:1>"
```

**10. No `gtag('set')` before the forwarded `page_view`** (GA4 layer)

```
Error: tag QA: journey "shared_link" (GA4 hit layer, production build) broke 2 rule(s): ga4-pii, ga4-params
  ✗ ga4-pii     request 2: dl "http://localhost/product/TL-DRK-001?ref=qa.friend%40example.com": email-shaped; the text typed into the site
  ✗ ga4-params  load 1 hit 2 view_item (dataLayer #6): dl: expected "http://localhost:5183/product/TL-DRK-001?ref=%5Bemail%5D", got "http://localhost/product/TL-DRK-001?ref=qa.friend%40example.com"
  …
```

**11. A second `purchase` 700 ms after the first**

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 3 rule(s): sequence, purchase-once, golden
  ✗ sequence       load 1: step 17 of the sequence: expected nothing more, got "purchase"
      …
  ✗ purchase-once  journey: purchase pushed 2 times for transaction_id TL-MUOAE7FW-01EGFHW (load 1 #24, load 1 #26)
  ✗ golden         tagqa/golden/purchase.json: the site's pushes differ from the golden:
      load 1 (golden 25 pushes, actual 27):
        + #25 { ecommerce: null }   unexpected (pushed, not in the golden)
        …
```

**12. Forwarding with GA4 off**

```
Error: tag QA: journey "purchase" (dataLayer layer, dev server) broke 1 rule(s): forwarding-off
  ✗ forwarding-off  load 1 #2 gtag set: pushed with GA4 forwarding off (this build has no measurement id)
  ✗ forwarding-off  load 1 #3 gtag event page_view: pushed with GA4 forwarding off (this build has no measurement id)
  …
```

## Is it flaky?

After review, three consecutive runs of the final 46-test suite on this machine (macOS, Chrome 154, 3 workers):
46 of 46 every time, in 45.6 to 45.7 s; 32 `/g/collect` requests and 121 site hits per run; a dataLayer journey 2.6
to 6.9 s (`two_orders` the longest), a GA4 journey 5.3 to 12.3 s (`consent` waits for gtag.js three times). The 1.5 s
final wait adds about 1.5 s to each journey. Also after review: the CI settings on Chrome, 46 of 46 in 1.0 min on 2
workers (above), and the twelve mutation runs, in which the tests a mutation does not touch passed every time.

Before the review, six consecutive runs of the 36-test suite, and two more after the last small edits: 36 of 36
passed every time, in 32.9 to 33.0 s (27 requests, 89 site hits). Before those, six runs of an earlier revision (35
tests) passed every time too, as did:

- a run with Vite's dependency cache deleted first, as on a fresh CI runner;
- a run of the dataLayer layer while two source files were touched every 2 s;
- the Stage 5 integration run (2026-09-30, after every other Stage 5 change was in): 36 of 36 in 33.0 s,
  ports 5182 and 5183 free afterwards.

The network is the one outside dependency. In a review's stressed run (datalayer and GA4 layers, each journey
three times, 6 workers) one GA4 journey failed with `ga4-script: gtag.js could not be downloaded from
www.googletagmanager.com: Error: route.fetch: read ETIMEDOUT`, then `ga4-sequence ... 0 hit(s)`, while its tags were
fine. The download is now tried up to three times, 1 s and 2 s apart (a server error counts as a failure to retry;
the `rules` project tests the retry), and once a worker has the script it serves it from memory. A longer outage still
fails the GA4 layer, with `ga4-script` naming the error; a rerun is then the answer. No timing-related failure was
seen in any run.

One problem did show up, during the first mutation run, and is fixed: the mutated file was written a
moment before the dev server started, macOS delivered the file event late, and Vite reloaded two pages
in the middle of their journeys (`expected 2 page load(s), got 3`, and in another test a Playwright
"execution context was destroyed"). The tag QA dev server now runs with the file watcher and HMR off
([`tagqa/vite.config.ts`](../site/tagqa/vite.config.ts)), and a page load a step did not ask for is
reported as an extra page load rather than a Playwright error (checked with a throwaway test that
reloads the page in the middle of a wait: two page loads recorded, three runs out of three). The run
with files being touched is the check that the reloads are gone.

## Limitations

- **One browser, one window.** Chrome locally, Chromium in CI, desktop size. No Safari or Firefox, no
  mobile viewport, no real devices.
- **gtag.js is Google's, and moves.** It is downloaded on every run (three tries), not pinned, because a copy of
  Google's script cannot be committed. A change on Google's side can fail the GA4 layer with no change
  to the site; the failure names the parameter. It also means the GA4 layer needs the network.
- **An absence is proven for 1.5 s.** A push that comes more than 1.5 s after a journey's last step is not seen by
  the dataLayer layer; the GA4 layer reads the recording again seconds later, after its last wait for gtag.js.
- **A second browser is simulated.** The `account` journey clears localStorage to stand for a second browser; it
  does not open one. That is enough here because the site's account directory lives in localStorage (a real backend
  would give the same id on every device, and the check would then need two accounts, not two browsers).
- **What GA4 does after the hit is not tested.** Processing, `transaction_id` deduplication, how an
  empty `uid` is stored, bot filtering, a real property's settings: nothing reaches Google, by design.
  The site's own export (Stage 2) is where collected data is checked, and
  [monitoring.md](monitoring.md) watches it.
- **No tag manager.** As in Stage 1, there is no GTM container, so nothing here tests how GTM would map
  these pushes to tags.
- **Scripted journeys only.** A path that is not in the plan is not covered: two tabs following each
  other's sign-in, landing on an unknown order id, a 404 page, a cart edited in another tab. Adding one
  is a journey in `plan.ts` plus `npm run tagqa:update`.
- **Each layer tests one build.** The dataLayer layer runs the dev server (StrictMode shows double
  effects), the GA4 layer the production build (what visitors get). The shared goldens tie the two
  together; neither layer alone covers both.
- **Consent is all or nothing.** The site has one Accept and one Reject, so only `G100` and `G111` are
  tested.
- **The goldens pin exact output.** An intended change to a push fails the suite until
  `npm run tagqa:update` is run and its diff reviewed. That is the point, and it is also a cost.
