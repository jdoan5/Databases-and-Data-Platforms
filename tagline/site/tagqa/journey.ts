/**
 * Driving a journey from plan.ts in the browser and recording every dataLayer push.
 *
 * The recorder is an init script that creates window.dataLayer before the site's code
 * runs (as a GTM snippet would) and wraps its push, so each push is copied to Node the
 * moment it happens, tagged with the page load it belongs to. That keeps pushes from
 * before a reload, and it sees pushes that bypass the site's tagging module. The site's
 * own wrapper (observeOutsidePushes) then wraps this one, so the site behaves as always.
 * If the page ever replaces window.dataLayer, settle() fails rather than record less.
 */
import { expect, type BrowserContext, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import type { Load, Push, Recording } from './entries'
import type { Step } from './plan'

const products = JSON.parse(readFileSync(new URL('../src/catalog/products.json', import.meta.url), 'utf8')) as {
  item_id: string
  item_name: string
  item_variant?: string
}[]
const displayName = (itemId: string) => {
  const p = products.find((x) => x.item_id === itemId)
  if (!p) throw new Error(`plan.ts names ${itemId}, which is not in the catalog`)
  return p.item_variant ? `${p.item_name} (${p.item_variant})` : p.item_name
}
const itemName = (itemId: string) => products.find((x) => x.item_id === itemId)?.item_name ?? itemId

declare global {
  interface Window {
    __tagqaLoad?: string
    __tagqaPush?: (load: string, json: string) => void
  }
}

/** Runs in the page before any of its scripts, on every page load. */
function recorderInitScript(): void {
  if (window !== window.top) return
  const load = `${Math.round(performance.timeOrigin)}-${Math.random().toString(36).slice(2, 8)}`
  window.__tagqaLoad = load
  const w = window as unknown as { dataLayer?: unknown[] }
  const dl = (w.dataLayer = w.dataLayer ?? [])
  Object.defineProperty(dl, '__tagqaLoad', { value: load })
  const push = dl.push
  dl.push = function (this: unknown[], ...entries: unknown[]): number {
    for (const entry of entries) {
      let json: string
      try {
        const value = Object.prototype.toString.call(entry) === '[object Arguments]' ? Array.from(entry as ArrayLike<unknown>) : entry
        json = JSON.stringify(value) ?? 'null'
      } catch {
        json = JSON.stringify(String(entry))
      }
      window.__tagqaPush?.(load, json)
    }
    return push.apply(this, entries)
  }
}

export class Recorder {
  private readonly loads = new Map<string, Push[]>()

  async install(context: BrowserContext): Promise<void> {
    await context.exposeBinding('__tagqaPush', (_source, load: string, json: string) => {
      if (!this.loads.has(load)) this.loads.set(load, [])
      this.loads.get(load)!.push(JSON.parse(json) as Push)
    })
    await context.addInitScript(recorderInitScript)
  }

  recording(): Recording {
    return { loads: [...this.loads.values()].map((pushes): Load => ({ pushes: [...pushes] })) }
  }

  /** Number of pushes recorded so far, over every page load. */
  count(): number {
    return [...this.loads.values()].reduce((n, p) => n + p.length, 0)
  }

  /**
   * Waits until every push of the current page load has reached Node and nothing new
   * has been pushed for `quietMs`. Anything a step causes is pushed by React effects
   * within milliseconds of the render, so 300 ms between steps is plenty; a push that
   * comes later still lands in the recording, since the next step's wait sees it. After
   * the last step there is no next step: driveJourney waits FINAL_QUIET_MS there, and a
   * push later than that is not seen (the GA4 layer also reads the recording again
   * after its final wait for gtag.js, several seconds on).
   */
  async settle(page: Page, quietMs = 300, timeoutMs = 10_000): Promise<void> {
    const deadline = Date.now() + timeoutMs
    let last = ''
    let since = Date.now()
    for (;;) {
      let state: { load: string; marker: string; length: number }
      try {
        state = await page.evaluate(() => {
          const dl = (window as unknown as { dataLayer?: unknown[] & { __tagqaLoad?: string } }).dataLayer
          return { load: window.__tagqaLoad ?? '', marker: dl?.__tagqaLoad ?? '', length: dl?.length ?? 0 }
        })
      } catch (err) {
        // A page load the step did not ask for (a reload, a redirect) destroys the context
        // mid-read. Keep settling on the new page: the recorder has its pushes, and the
        // sequence rule reports the extra page load.
        if (!/context was destroyed|navigat/i.test(String(err)) || Date.now() > deadline) throw err
        await page.waitForTimeout(100)
        continue
      }
      if (state.marker !== state.load) {
        throw new Error('window.dataLayer was replaced after the page loaded: later pushes would bypass the recorder (and any tag manager)')
      }
      const recorded = this.loads.get(state.load)?.length ?? 0
      const key = `${state.load}:${state.length}:${recorded}`
      if (key !== last) {
        last = key
        since = Date.now()
      } else if (recorded === state.length && Date.now() - since >= quietMs) {
        return
      }
      if (Date.now() > deadline) {
        throw new Error(`the dataLayer did not settle within ${timeoutMs / 1000}s (${recorded} of ${state.length} pushes recorded)`)
      }
      await page.waitForTimeout(50)
    }
  }
}

// ---- steps ---------------------------------------------------------------------------

export function describeStep(step: Step): string {
  switch (step.do) {
    case 'open':
      return `open ${step.path}`
    case 'consent':
      return `cookie banner: ${step.choice === 'accept' ? 'Accept' : 'Reject'}`
    case 'category':
      return `category: ${step.name}`
    case 'selectItem':
      return `select ${step.itemId} in the list`
    case 'addToCart':
      return `add ${step.quantity} to the cart`
    case 'cartMore':
      return `cart: one more ${step.itemId}`
    case 'cartLess':
      return `cart: one fewer ${step.itemId}`
    case 'cartRemove':
      return `cart: remove ${step.itemId}`
    case 'shipping':
      return `shipping: ${step.tier}`
    case 'payment':
      return `payment: ${step.type}`
    case 'search':
      return `search for "${step.term}"`
    case 'signIn':
      return step.mode === 'sign_up' ? 'create an account' : 'sign in'
    case 'clearStorage':
      return "clear the browser's localStorage (as in a second browser)"
    default:
      return step.do
  }
}

/** True for the steps that start a new page load. */
export const startsPageLoad = (step: Step) => step.do === 'open' || step.do === 'reload'

const h1 = (page: Page, name: string | RegExp) => page.getByRole('heading', { level: 1, name, exact: typeof name === 'string' })
const mainH1 = (page: Page) => page.locator('main h1').first()

export async function runStep(page: Page, step: Step): Promise<void> {
  switch (step.do) {
    case 'open':
      await page.goto(step.path)
      await expect(mainH1(page)).toBeVisible()
      return
    case 'reload':
      await page.reload()
      await expect(mainH1(page)).toBeVisible()
      return
    case 'back':
    case 'forward': {
      const before = page.url()
      await (step.do === 'back' ? page.goBack() : page.goForward())
      await expect(page).not.toHaveURL(before)
      await expect(mainH1(page)).toBeVisible()
      return
    }
    case 'consent': {
      const banner = page.getByRole('region', { name: 'Cookies' })
      await banner.getByRole('button', { name: step.choice === 'accept' ? 'Accept' : 'Reject' }).click()
      await expect(banner).toHaveCount(0)
      return
    }
    case 'cookieSettings':
      await page.getByRole('button', { name: 'Cookie settings' }).click()
      await expect(page.getByRole('region', { name: 'Cookies' })).toBeVisible()
      return
    case 'brand':
      await page.locator('header a.brand').click()
      await expect(h1(page, 'All products')).toBeVisible()
      return
    case 'category':
      await page.getByRole('navigation', { name: 'Categories' }).getByRole('link', { name: step.name, exact: true }).click()
      await expect(page).toHaveURL((u) => u.pathname === `/category/${step.name.toLowerCase()}`)
      await expect(h1(page, step.name)).toBeVisible()
      return
    case 'selectItem':
      await page.locator(`main a[data-item-id="${step.itemId}"]`).click()
      await expect(page).toHaveURL((u) => u.pathname === `/product/${step.itemId}`)
      await expect(h1(page, itemName(step.itemId))).toBeVisible()
      return
    case 'addToCart':
      await page.getByLabel('Quantity').selectOption(String(step.quantity))
      await page.getByRole('button', { name: 'Add to cart' }).click()
      await expect(page.getByRole('status')).toContainText(`Added ${step.quantity}`)
      return
    case 'cart':
      await page.locator('header a.cart-link').click()
      await expect(h1(page, 'Cart')).toBeVisible()
      return
    case 'cartMore':
    case 'cartLess': {
      const name = displayName(step.itemId)
      const quantity = page.getByRole('group', { name: `Quantity of ${name}` }).locator('span[aria-live]')
      const before = Number(await quantity.textContent())
      await page.getByRole('button', { name: `${step.do === 'cartMore' ? 'One more' : 'One fewer'} ${name}` }).click()
      if (step.do === 'cartLess' && before === 1) await expect(quantity).toHaveCount(0)
      else await expect(quantity).toHaveText(String(step.do === 'cartMore' ? before + 1 : before - 1))
      return
    }
    case 'cartRemove':
      await page.locator(`[data-remove="${step.itemId}"]`).click()
      await expect(page.locator(`[data-remove="${step.itemId}"]`)).toHaveCount(0)
      return
    case 'checkout':
      await page.getByRole('link', { name: 'Checkout' }).click()
      await expect(h1(page, 'Checkout')).toBeVisible()
      return
    case 'shipping':
      await page.getByLabel('Shipping').selectOption(step.tier)
      return
    case 'payment':
      await page.getByLabel('Payment type').selectOption(step.type)
      return
    case 'placeOrder':
      await page.getByRole('button', { name: 'Place order' }).click()
      await expect(h1(page, 'Order confirmed')).toBeVisible()
      return
    case 'search': {
      const before = page.url()
      const box = page.locator('#site-search')
      await box.fill(step.term)
      await box.press('Enter')
      await expect(page).toHaveURL((u) => u.pathname === '/search' && u.href !== before)
      await expect(h1(page, /^Results for/)).toBeVisible()
      return
    }
    case 'signInLink':
      await page.locator('header').getByRole('link', { name: 'Sign in' }).click()
      await expect(h1(page, 'Sign in')).toBeVisible()
      return
    case 'signIn':
      await page.locator('#email').fill(step.email)
      await page.getByRole('button', { name: step.mode === 'sign_up' ? 'Create account' : 'Sign in', exact: true }).click()
      await expect(h1(page, 'Signed in')).toBeVisible()
      return
    case 'signOut':
      await page.locator('header').getByRole('button', { name: 'Sign out' }).click()
      await expect(page.locator('header').getByRole('link', { name: 'Sign in' })).toBeVisible()
      return
    case 'clearStorage':
      // All of it, not one key: whatever the site keeps its accounts under, a second browser has none of it.
      await page.evaluate(() => localStorage.clear())
      await expect.poll(() => page.evaluate(() => localStorage.length)).toBe(0)
      return
  }
}
