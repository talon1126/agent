import { test, expect } from '@playwright/test'

test('renders the TalonMart storefront shell', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('link', { name: 'TalonMart home' })).toBeVisible()
  await expect(page.getByLabel('Search products')).toBeVisible()
  await expect(page.getByText('Today deals').first()).toBeVisible()
  await expect(page.getByRole('link', { name: 'Cart' })).toBeVisible()
})
