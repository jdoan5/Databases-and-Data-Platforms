/**
 * The seeded plan: who visits, on which devices, from which channel, and what they do.
 *
 * Pure and deterministic: the same options always give the same plan (a unit test
 * checks it), and nothing here touches a browser. The runner (run.js) only executes
 * the steps it is given, so every difference between two runs is a difference in the
 * plan, which can be printed and read with --plan-only.
 *
 * Two inputs:
 *   - `population` fixes who the customers are: person 7 always has the same synthetic
 *     email and the same account id, so separate runs (different seeds, different days)
 *     bring the same signed-in customers back, as a real store's customers come back.
 *   - `seed` fixes what they do this run: devices, channels, journeys, consent choices.
 * Each person draws from a random stream of their own, so the first 10 people of a
 * 50-person plan are exactly the 10 people of a 10-person plan with the same seed.
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

export const PRODUCTS = JSON.parse(readFileSync(new URL('../../site/src/catalog/products.json', import.meta.url), 'utf8'))
export const CATEGORIES = ['Apparel', 'Drinkware', 'Bags', 'Office', 'Stickers']
export const categorySlug = (category) => category.toLowerCase()

/**
 * The paid and owned campaigns. The same source / medium / campaign triples are what
 * the synthetic campaign_costs table must carry for mart_campaign_daily to join cost.
 */
export const CAMPAIGNS = {
  fall_launch: { source: 'google', medium: 'cpc', campaign: 'fall_launch', terms: ['logo hoodie', 'tagline tee', 'crewneck sweatshirt'] },
  newsletter_oct: { source: 'newsletter', medium: 'email', campaign: 'newsletter_oct', contents: ['hero', 'product_grid', 'footer'] },
  retarget_q4: { source: 'facebook', medium: 'paid_social', campaign: 'retarget_q4', contents: ['carousel', 'single_image'] },
}

/** organic = arrives from a Google results page (referrer, no UTM); direct = no referrer, no UTM. */
export const CHANNELS = [...Object.keys(CAMPAIGNS), 'organic', 'direct']
export const ORGANIC_REFERRER = 'https://www.google.com/'

export const SHIPPING_TIERS = ['Ground', 'Express', 'Next Day']
export const PAYMENT_TYPES = ['Credit Card', 'PayPal', 'Gift Card']

/** Search terms people type. All but the last find something; 'umbrella' finds nothing. */
const SEARCH_TERMS = ['mug', 'tee', 'hoodie', 'bottle', 'sticker', 'backpack', 'notebook', 'grey tee', 'desk mat', 'tote', 'umbrella']

/**
 * Window sizes only. The user agent is never changed, so GA4 sees every device as
 * the same desktop Chrome on macOS; the size changes which layout the site renders.
 */
const WINDOWS = {
  phone: { width: 390, height: 844 },
  laptop: { width: 1280, height: 800 },
  desktop: { width: 1440, height: 900 },
}

// Weights. Illustrative, not calibrated against anything: enough spread that every
// channel has sessions, orders and abandoned carts in a 50-person run.
const DEVICE_COUNT = { 1: 0.6, 2: 0.3, 3: 0.1 }
// A retargeting ad only reaches someone who has visited before, so retarget_q4 is never a
// person's first device (it can bring them back on another device).
const FIRST_CHANNEL = { fall_launch: 0.3, newsletter_oct: 0.2, organic: 0.25, direct: 0.25 }
const LATER_CHANNEL = { direct: 0.4, organic: 0.15, newsletter_oct: 0.18, retarget_q4: 0.22, fall_launch: 0.05 }
const FIRST_DEPTH = {
  fall_launch: { bounce: 0.3, browse: 0.3, cart_abandon: 0.17, checkout_abandon: 0.08, purchase: 0.15 },
  newsletter_oct: { bounce: 0.2, browse: 0.3, cart_abandon: 0.15, checkout_abandon: 0.1, purchase: 0.25 },
  retarget_q4: { bounce: 0.25, browse: 0.25, cart_abandon: 0.2, checkout_abandon: 0.1, purchase: 0.2 },
  organic: { bounce: 0.25, browse: 0.35, cart_abandon: 0.15, checkout_abandon: 0.1, purchase: 0.15 },
  direct: { bounce: 0.25, browse: 0.3, cart_abandon: 0.15, checkout_abandon: 0.1, purchase: 0.2 },
}
const LATER_DEPTH = { bounce: 0.1, browse: 0.2, cart_abandon: 0.15, checkout_abandon: 0.1, purchase: 0.45 }
const CONSENT = { accept: 0.72, reject: 0.18, ignore: 0.1 }

const LANDINGS = {
  fall_launch: { '/category/apparel': 0.5, '/product/TL-APP-003': 0.2, '/product/TL-APP-004': 0.15, '/': 0.15 },
  newsletter_oct: { '/': 0.4, '/category/drinkware': 0.3, '/product/TL-DRK-003': 0.3 },
  organic: { '/': 0.45, '/category/bags': 0.15, '/category/office': 0.1, '/product/TL-DRK-001': 0.15, '/product/TL-BAG-002': 0.15 },
  direct: { '/': 1 },
}

export const DEFAULTS = { people: 50, seed: 'tagline', population: 'tagline' }

// ---------------------------------------------------------------------------------
// Random streams

/** mulberry32 seeded from a SHA-256 of the parts: small, fast, good enough for plans. */
export function rngFrom(...parts) {
  let a = createHash('sha256').update(parts.join('\u0000')).digest().readUInt32LE(0)
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const int = (rng, lo, hi) => lo + Math.floor(rng() * (hi - lo + 1))
const chance = (rng, p) => rng() < p
const pick = (rng, list) => list[Math.floor(rng() * list.length)]
function weighted(rng, weights) {
  const entries = Object.entries(weights)
  const total = entries.reduce((s, [, w]) => s + w, 0)
  let r = rng() * total
  for (const [key, w] of entries) {
    r -= w
    if (r < 0) return key
  }
  return entries[entries.length - 1][0]
}

// ---------------------------------------------------------------------------------
// The site's own rules, mirrored so the planner knows what each page will list

export function productsInCategory(category) {
  return PRODUCTS.filter((p) => p.item_category === category)
}

/** Same rule as searchProducts() in site/src/catalog/catalog.ts: every word must match. */
export function searchProducts(term) {
  const words = term.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  return PRODUCTS.filter((p) => {
    const haystack = [p.item_name, p.item_category, p.item_variant ?? ''].join(' ').toLowerCase()
    return words.every((w) => haystack.includes(w))
  })
}

export function displayName(p) {
  return p.item_variant ? `${p.item_name} (${p.item_variant})` : p.item_name
}

// ---------------------------------------------------------------------------------
// People

/**
 * Who person n is. The account id stands in for what the store's account service
 * returns for this customer; it is a hash of the population and the number, so it is
 * stable across runs and says nothing about the email.
 */
export function identity(n, population) {
  const tag = String(n).padStart(3, '0')
  const accountId = createHash('sha256').update(`tagline-sim/account/v1\u0000${population}\u0000${n}`).digest('hex').slice(0, 32)
  return { personId: `p${tag}`, email: `${population}-shopper-${tag}@tagline-sim.example`, accountId }
}

function landingFor(rng, channel) {
  if (channel === 'retarget_q4') return `/product/${pick(rng, PRODUCTS).item_id}`
  return weighted(rng, LANDINGS[channel])
}

/**
 * The campaign's landing URL. With `email`, the link also carries the subscriber's
 * address, as email-service merge tags often do: the site must redact it from every
 * page_location it sends and keep the utm_* parameters next to it.
 */
function landingUrl(rng, channel, path, email = null) {
  const c = CAMPAIGNS[channel]
  if (!c) return path
  const q = [['utm_source', c.source], ['utm_medium', c.medium], ['utm_campaign', c.campaign]]
  if (c.terms) q.push(['utm_term', pick(rng, c.terms)])
  if (c.contents) q.push(['utm_content', pick(rng, c.contents)])
  if (email) q.push(['subscriber', email])
  return `${path}?${q.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`
}

/** What the landing path shows: a list (with its product ids) or one product. */
function pageFor(path) {
  if (path === '/') return { type: 'list', items: PRODUCTS.map((p) => p.item_id), category: null }
  const cat = path.match(/^\/category\/([a-z]+)$/)
  if (cat) {
    const category = CATEGORIES.find((c) => categorySlug(c) === cat[1])
    return { type: 'list', items: productsInCategory(category).map((p) => p.item_id), category }
  }
  const product = path.match(/^\/product\/(TL-[A-Z]{3}-\d{3})$/)
  if (product) return { type: 'product', itemId: product[1] }
  return { type: 'other' }
}

/**
 * One device's steps. The planner keeps a model of the page the visitor is on, so a
 * step is only planned where the site can do it: a product is clicked from a list that
 * shows it, "add" happens on a product page, checkout needs a non-empty cart.
 */
function journey(rng, { path, url, referrer, depth, signIn, consent, email, emailInSearch }) {
  const steps = []
  const think = (lo, hi) => int(rng, lo, hi)
  let page = pageFor(path)
  let lastSearch = null
  let probed = false

  steps.push({ do: 'land', url, ...(referrer ? { referrer } : {}), thinkMs: think(700, 2000) })
  if (consent !== 'ignore') steps.push({ do: 'consent', choice: consent, thinkMs: think(400, 1400) })

  const signInAt = !signIn
    ? null
    : signIn === 'login'
      ? weighted(rng, { start: 0.7, before_cart: 0.3 })
      : weighted(rng, { start: 0.25, before_cart: 0.45, end: 0.3 })
  let signedIn = false
  const doSignIn = () => {
    steps.push({ do: 'sign_in', mode: signIn, email, thinkMs: think(500, 1500) })
    page = pageFor('/') // the runner goes "Back to the store" afterwards
    signedIn = true
  }

  const toCategory = () => {
    const options = CATEGORIES.filter((c) => c !== page.category)
    const category = pick(rng, options)
    steps.push({ do: 'category', name: category, slug: categorySlug(category), thinkMs: think(500, 1800) })
    page = { type: 'list', items: productsInCategory(category).map((p) => p.item_id), category }
  }
  const toSearch = (term) => {
    steps.push({ do: 'search', term, thinkMs: think(600, 1800) })
    // The site replaces an email typed into the box with [email] before searching.
    const results = term.includes('@') ? [] : searchProducts(term)
    page = { type: 'list', items: results.map((p) => p.item_id), category: null }
    lastSearch = term
  }
  const toProduct = (avoid = []) => {
    let choices = page.type === 'list' ? page.items.filter((id) => !avoid.includes(id)) : []
    if (choices.length === 0) {
      toCategory()
      choices = page.items.filter((id) => !avoid.includes(id))
      if (choices.length === 0) choices = page.items
    }
    const itemId = pick(rng, choices)
    steps.push({ do: 'product', itemId, thinkMs: think(800, 2500) })
    page = { type: 'product', itemId }
  }

  if (signInAt === 'start') doSignIn()

  if (depth === 'bounce') {
    steps[steps.length - 1].thinkMs = think(1500, 5000)
    return steps
  }

  // Browsing before (or instead of) buying.
  const browses = depth === 'browse' ? int(rng, 1, 3) : int(rng, 0, 2)
  for (let i = 0; i < browses; i++) {
    const action = weighted(rng, { category: 0.4, search: 0.3, product: 0.3 })
    if (action === 'category') toCategory()
    else if (action === 'search') {
      const probe = emailInSearch && !probed
      probed ||= probe
      toSearch(probe ? email : pick(rng, SEARCH_TERMS.filter((t) => t !== lastSearch)))
    } else toProduct()
  }
  // A probe planned on purpose: this visitor pastes their email into the search box.
  // The site must keep it out of every hit; the dry run's PII check proves it did.
  if (emailInSearch && !probed) {
    probed = true
    toSearch(email)
  }

  if (depth === 'browse') {
    if (signIn && !signedIn) doSignIn()
    return steps
  }

  // Cart building: one to three products.
  const lines = new Map()
  const lineCount = Number(weighted(rng, { 1: 0.6, 2: 0.3, 3: 0.1 }))
  while (lines.size < lineCount) {
    if (page.type !== 'product' || lines.has(page.itemId)) {
      if (chance(rng, 0.3)) toSearch(pick(rng, SEARCH_TERMS.slice(0, -1).filter((t) => t !== lastSearch)))
      toProduct([...lines.keys()])
    }
    if (lines.has(page.itemId)) break // every product in reach is already in the cart
    const quantity = Number(weighted(rng, { 1: 0.7, 2: 0.2, 3: 0.1 }))
    steps.push({ do: 'add', itemId: page.itemId, quantity, thinkMs: think(400, 1200) })
    lines.set(page.itemId, quantity)
  }

  if (signInAt === 'before_cart' && !signedIn) doSignIn()

  steps.push({ do: 'cart', thinkMs: think(600, 1800) })
  page = { type: 'other' }
  const units = [...lines.values()].reduce((a, b) => a + b, 0)
  if (units > 1 && chance(rng, 0.2)) {
    const [itemId] = pick(rng, [...lines.entries()])
    steps.push({ do: 'cart_less', itemId, thinkMs: think(400, 1000) })
  }

  if (depth !== 'cart_abandon') {
    steps.push({ do: 'checkout', thinkMs: think(700, 2000) })
    if (depth === 'purchase' || chance(rng, 0.5)) {
      steps.push({ do: 'shipping', tier: weighted(rng, { Ground: 0.6, Express: 0.3, 'Next Day': 0.1 }), thinkMs: think(400, 1200) })
    }
    if (depth === 'purchase') {
      steps.push({ do: 'payment', type: weighted(rng, { 'Credit Card': 0.65, PayPal: 0.25, 'Gift Card': 0.1 }), thinkMs: think(400, 1200) })
      steps.push({ do: 'place_order', thinkMs: think(1000, 2500) })
    }
  }

  if (signIn && !signedIn) doSignIn()
  return steps
}

function windowFor(rng, used) {
  const fresh = Object.keys(WINDOWS).filter((k) => !used.includes(k))
  const kind = used.length === 0 ? weighted(rng, { phone: 0.5, desktop: 0.3, laptop: 0.2 }) : pick(rng, fresh.length ? fresh : Object.keys(WINDOWS))
  return { kind, ...WINDOWS[kind] }
}

function person(n, seed, population) {
  const id = identity(n, population)
  const rng = rngFrom('tagline-sim/journey/v1', seed, population, n)

  const deviceCount = Number(weighted(rng, DEVICE_COUNT))
  // Signing in on several devices is what makes cross-device stitching real: sign_up
  // on the first device, login on later ones. A few single-device people sign up, and
  // a few log in to an account they opened before this run.
  const signsUp = deviceCount > 1 ? chance(rng, 0.75) : chance(rng, 0.15)
  const hasAccount = !signsUp && deviceCount === 1 && chance(rng, 0.1)

  const devices = []
  const usedWindows = []
  for (let d = 1; d <= deviceCount; d++) {
    const first = d === 1
    const channel = weighted(rng, first ? FIRST_CHANNEL : LATER_CHANNEL)
    const signIn = first ? (signsUp ? 'sign_up' : hasAccount ? 'login' : null) : signsUp && chance(rng, 0.85) ? 'login' : null
    let depth = weighted(rng, first ? FIRST_DEPTH[channel] : LATER_DEPTH)
    const emailInSearch = first && n % 20 === 7
    if ((signIn || emailInSearch) && depth === 'bounce') depth = 'browse'
    const consent = weighted(rng, CONSENT)
    const win = windowFor(rng, usedWindows)
    usedWindows.push(win.kind)
    const path = landingFor(rng, channel)
    const emailInUrl = channel === 'newsletter_oct' && chance(rng, 0.35)
    const url = landingUrl(rng, channel, path, emailInUrl ? id.email : null)
    const referrer = channel === 'organic' ? ORGANIC_REFERRER : null
    devices.push({
      deviceId: `${id.personId}-d${d}`,
      channel,
      window: win,
      consent,
      signIn,
      depth,
      landing: url,
      ...(referrer ? { referrer } : {}),
      ...(emailInSearch || emailInUrl ? { probes: [...(emailInUrl ? ['email_in_landing_url'] : []), ...(emailInSearch ? ['email_in_search'] : [])] } : {}),
      gapBeforeMs: first ? 0 : int(rng, 500, 2500),
      steps: journey(rng, { path, url, referrer, depth, signIn, consent, email: id.email, emailInSearch }),
    })
  }
  return { ...id, devices }
}

export function buildPlan(options = {}) {
  const { people, seed, population } = { ...DEFAULTS, ...options }
  if (!Number.isInteger(people) || people < 1 || people > 2000) throw new Error('people must be an integer from 1 to 2000')
  if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(population)) throw new Error('population must be 1-32 lowercase letters, digits or hyphens')
  return {
    version: 1,
    seed: String(seed),
    population,
    people: Array.from({ length: people }, (_, i) => person(i + 1, String(seed), population)),
  }
}

/** Counts a reader can check a run against. */
export function planStats(plan) {
  const devices = plan.people.flatMap((p) => p.devices)
  const count = (key) => devices.reduce((acc, d) => ({ ...acc, [d[key] ?? 'none']: (acc[d[key] ?? 'none'] ?? 0) + 1 }), {})
  return {
    people: plan.people.length,
    devices: devices.length,
    crossDevicePeople: plan.people.filter((p) => p.devices.filter((d) => d.signIn).length > 1).length,
    byChannel: count('channel'),
    byDepth: count('depth'),
    byConsent: count('consent'),
    bySignIn: count('signIn'),
    purchases: devices.filter((d) => d.steps.some((s) => s.do === 'place_order')).length,
    steps: devices.reduce((n, d) => n + d.steps.length, 0),
    plannedThinkSeconds: Math.round(devices.reduce((n, d) => n + d.steps.reduce((m, s) => m + s.thinkMs, 0), 0) / 1000),
  }
}
