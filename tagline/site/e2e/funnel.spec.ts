import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createContract, type JsonSchema } from '../src/tagging/contract.ts'

/**
 * The purchase funnel, end to end, in real Chrome.
 *
 * Reads window.dataLayer straight out of the page and validates every entry in Node
 * against tagging/events.schema.json, independently of the site's own runtime check.
 */

const schema = JSON.parse(
  readFileSync(new URL('../../tagging/events.schema.json', import.meta.url), 'utf8'),
) as JsonSchema
const contract = createContract(schema)

type Entry = Record<string, unknown> | unknown[]

/** window.dataLayer with gtag() Arguments objects turned into arrays, as the schema describes them. */
async function readDataLayer(page: Page): Promise<Entry[]> {
  return page.evaluate(() => {
    const dl = (window as unknown as { dataLayer?: unknown[] }).dataLayer ?? []
    return JSON.parse(
      JSON.stringify(dl.map((e) => (Object.prototype.toString.call(e) === '[object Arguments]' ? Array.from(e as ArrayLike<unknown>) : e))),
    ) as Entry[]
  })
}

const eventName = (e: Entry): string | undefined =>
  !Array.isArray(e) && typeof e.event === 'string' ? e.event : undefined

const eventNames = (dl: Entry[]) => dl.map(eventName).filter((n): n is string => n !== undefined)

function expectAllValid(dl: Entry[]) {
  const failures = dl
    .map((entry, i) => ({ i, entry, check: contract.check(entry) }))
    .filter(({ check }) => !check.valid)
    .map(({ i, check }) => `#${i} ${check.label}: ${check.errors.join('; ')}`)
  expect(failures, 'every dataLayer entry matches the contract').toEqual([])
}

const ECOMMERCE = new Set([
  'view_item_list',
  'select_item',
  'view_item',
  'add_to_cart',
  'remove_from_cart',
  'view_cart',
  'begin_checkout',
  'add_shipping_info',
  'add_payment_info',
  'purchase',
])

test('funnel: every push valid, events in order, purchase exactly once across a reload', async ({ page }) => {
  const warnings: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'warning' || msg.type() === 'error') warnings.push(msg.text())
  })
  // With no GA4 measurement id (the config forces it empty), nothing may leave the machine.
  const offsite: string[] = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (url.protocol !== 'data:' && url.hostname !== 'localhost') offsite.push(req.url())
  })

  // Home, with the Tag Inspector switched on.
  await page.goto('/?debug=1')
  await page.getByRole('region', { name: 'Cookies' }).getByRole('button', { name: 'Accept' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'All products' })).toBeVisible()

  // select_item → product page.
  await page.locator('a[data-item-id="TL-DRK-001"]').click()
  await expect(page.getByRole('heading', { level: 1, name: 'Ceramic Mug' })).toBeVisible()

  // add_to_cart, 2 units.
  await page.getByLabel('Quantity').selectOption('2')
  await page.getByRole('button', { name: 'Add to cart' }).click()
  await expect(page.getByRole('status')).toContainText('Added 2')

  // view_cart → begin_checkout.
  await page.getByRole('link', { name: 'View cart' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Cart' })).toBeVisible()
  // The header's Cart link, on the cart page: a click on a link to the page already
  // shown is not a new page, so no second page_view or view_cart. The exact event
  // order below would catch one.
  await page.getByRole('link', { name: /^Cart/ }).click()
  await page.getByRole('link', { name: 'Checkout' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Checkout' })).toBeVisible()

  // add_shipping_info, add_payment_info, then place the order.
  await page.getByLabel('Shipping').selectOption('Express')
  await page.getByLabel('Payment type').selectOption('Credit Card')
  await page.getByRole('button', { name: 'Place order' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Order confirmed' })).toBeVisible()
  const transactionId = (await page.getByTestId('transaction-id').textContent())?.trim()
  expect(transactionId).toMatch(/^TL-[A-Z0-9]+-[A-Z0-9]+$/)

  await expect.poll(async () => eventNames(await readDataLayer(page)).at(-1)).toBe('purchase')
  const dl = await readDataLayer(page)

  // 1. The exact event order, page views included.
  expect(eventNames(dl)).toEqual([
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
  ])

  // 2. Every push matches the contract: consent commands, ecommerce clears and events.
  expectAllValid(dl)

  // 3. Consent default is the very first push; the Accept click is an update.
  expect(dl[0]).toEqual([
    'consent',
    'default',
    { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' },
  ])
  expect(dl).toContainEqual([
    'consent',
    'update',
    { analytics_storage: 'granted', ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted' },
  ])

  // 4. { ecommerce: null } immediately before every ecommerce event.
  dl.forEach((entry, i) => {
    const name = eventName(entry)
    if (name && ECOMMERCE.has(name)) expect(dl[i - 1], `clear before ${name}`).toEqual({ ecommerce: null })
  })

  // 5. The purchase itself: 2 × 13.99, Express shipping, 8% tax on the subtotal.
  const purchases = dl.filter((e) => eventName(e) === 'purchase')
  expect(purchases).toHaveLength(1)
  expect(purchases[0]).toMatchObject({
    ecommerce: {
      transaction_id: transactionId,
      currency: 'USD',
      value: 27.98,
      tax: 2.24,
      shipping: 12,
      items: [{ item_id: 'TL-DRK-001', quantity: 2, price: 13.99 }],
    },
  })

  // 6. The Tag Inspector agrees.
  await page.getByRole('button', { name: /Tag Inspector/ }).click()
  const inspector = page.getByRole('complementary', { name: 'Tag Inspector' })
  await expect(inspector).toContainText('all valid')
  await expect(inspector.locator('.tag-name').first()).toHaveText('purchase')
  await inspector.getByRole('button', { name: 'Close Tag Inspector' }).click()

  // 7. Reload the confirmation page: a fresh dataLayer, and no second purchase.
  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'Order confirmed' })).toBeVisible()
  await expect(page.getByTestId('purchase-status')).toContainText('earlier visit')
  await expect.poll(async () => eventNames(await readDataLayer(page))).toContain('page_view')
  // Proving an absence needs a pause: give any stray effect time to push before reading.
  await page.waitForTimeout(500)
  const afterReload = await readDataLayer(page)
  expect(eventNames(afterReload)).toEqual(['page_view'])
  expectAllValid(afterReload)
  const claimed = await page.evaluate(() => localStorage.getItem('tagline.purchases_sent'))
  expect(JSON.parse(claimed ?? '[]')).toContain(transactionId)

  expect(warnings.filter((w) => w.includes('[tagline]'))).toEqual([])
  expect(offsite, 'requests to anything but the local dev server').toEqual([])
})
