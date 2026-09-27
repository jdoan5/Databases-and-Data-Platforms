import data from './products.json'

export const CATEGORIES = ['Apparel', 'Drinkware', 'Bags', 'Office', 'Stickers'] as const
export type Category = (typeof CATEGORIES)[number]

export interface Product {
  item_id: string
  item_name: string
  item_category: Category
  item_variant?: string
  /** USD, before tax and shipping. */
  price: number
  /** Display only: a CSS colour standing in for a product photo. Never tagged. */
  swatch: string
  /** Display only. Never tagged. */
  description: string
}

export const products: readonly Product[] = data as Product[]

const byId = new Map(products.map((p) => [p.item_id, p]))

export function findProduct(itemId: string | undefined): Product | undefined {
  return itemId ? byId.get(itemId) : undefined
}

export function categorySlug(category: Category): string {
  return category.toLowerCase()
}

export function categoryFromSlug(slug: string | undefined): Category | undefined {
  return CATEGORIES.find((c) => categorySlug(c) === slug)
}

export function productsInCategory(category: Category): Product[] {
  return products.filter((p) => p.item_category === category)
}

/** Case-insensitive match on name, category and variant. Every word must match somewhere. */
export function searchProducts(term: string): Product[] {
  const words = term.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  return products.filter((p) => {
    const haystack = [p.item_name, p.item_category, p.item_variant ?? ''].join(' ').toLowerCase()
    return words.every((w) => haystack.includes(w))
  })
}

export function displayName(p: Pick<Product, 'item_name' | 'item_variant'>): string {
  return p.item_variant ? `${p.item_name} (${p.item_variant})` : p.item_name
}

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
export const formatUSD = (n: number): string => usd.format(n)
