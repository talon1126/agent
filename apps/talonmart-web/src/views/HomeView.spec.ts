import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HomeView from './HomeView.vue'

const routerPush = vi.fn()
const { fetchFlashSales, fetchHomeHotRankings, purchaseFlashSaleWithDefaultAddress } = vi.hoisted(() => ({
  fetchFlashSales: vi.fn(),
  fetchHomeHotRankings: vi.fn(),
  purchaseFlashSaleWithDefaultAddress: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a><slot /></a>',
  },
  useRouter: () => ({
    push: routerPush,
  }),
}))

vi.mock('@/services/flashSaleApi', () => ({
  fetchFlashSales,
  purchaseFlashSaleWithDefaultAddress,
}))

vi.mock('@/services/categoryRankingApi', () => ({
  fetchHomeHotRankings,
}))

describe('HomeView', () => {
  beforeEach(() => {
    routerPush.mockClear()
    purchaseFlashSaleWithDefaultAddress.mockReset()
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 1,
      flash_sales: [
        {
          id: 2,
          item_id: 'item_milk_pure',
          sale_price: 12.9,
          stock_limit: 30,
          stock_remaining: 30,
          status: 'active',
          starts_at: '2026-06-01T17:04:35+08:00',
          ends_at: '2026-06-09T17:04:35+08:00',
        },
      ],
    })
    fetchHomeHotRankings.mockReset()
    fetchHomeHotRankings.mockResolvedValue({
      ok: true,
      rank_type: 'hot',
      window_type: 'all_time',
      count: 1,
      items: [
        {
          rank: 3,
          item_id: 'item_wireless_earbuds',
          item_name: 'Wireless Earbuds',
          brand: 'Talon Audio',
          spec: 'Bluetooth 5.3',
          category_id: 'electronics',
          category_name: 'Electronics',
          price: 59.99,
          score: 92,
        },
      ],
    })
  })

  it('renders the TalonMart storefront essentials with the promotional carousel', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.get('[data-testid="store-header-logo"]').attributes('aria-label')).toBe(
      'TalonMart home',
    )
    expect(wrapper.get('input[aria-label="Search products"]').attributes('placeholder')).toContain(
      'Search',
    )
    expect(wrapper.find('[data-testid="home-hero-carousel"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Weekend cart refresh')
    expect(wrapper.text()).not.toContain('Shop by department')
    expect(wrapper.text()).not.toContain('Today deals')
    expect(wrapper.text()).not.toContain('Ready to ship')
    expect(wrapper.text()).toContain('Flash Deals')
    expect(wrapper.text()).toContain('Pure milk flash deal')
    expect(wrapper.text()).toContain('Bet you like it.')
    expect(wrapper.text()).toContain('Wireless Earbuds')
    expect(wrapper.text()).toContain('#3 in Electronics')
    expect(wrapper.text()).toContain('30 left')
    expect(wrapper.text()).toContain('Cart')
  })

  it('switches the hero carousel from the next control', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.get('[data-testid="home-hero-track"]').attributes('style')).toContain(
      'translate3d(0%, 0, 0)',
    )

    await wrapper.get('[data-testid="home-hero-next"]').trigger('click')

    expect(wrapper.get('[data-testid="home-hero-track"]').attributes('style')).toContain(
      'translate3d(-100%, 0, 0)',
    )
    expect(wrapper.get('[data-testid="home-hero-track"]').classes()).toContain(
      'transition-transform',
    )
  })

  it('renders homepage product cards without visible product borders', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.get('[data-testid="flash-sale-card"]').classes()).not.toContain('border')
    expect(wrapper.get('[data-testid="flash-sale-detail-item_milk_pure"]').classes()).not.toContain(
      'border',
    )
  })

  it('routes flash deal product clicks to the product detail page', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    await wrapper.get('[data-testid="flash-sale-detail-item_milk_pure"]').trigger('click')

    expect(routerPush).toHaveBeenCalledWith({
      name: 'product-detail',
      params: { item_id: 'item_milk_pure' },
    })
  })

  it('routes hot ranking product clicks to the product detail page', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    await wrapper.get('[data-testid="home-hot-product-item_wireless_earbuds"]').trigger('click')

    expect(routerPush).toHaveBeenCalledWith({
      name: 'product-detail',
      params: { item_id: 'item_wireless_earbuds' },
    })
  })

  it('shows backend discount price when a flash deal item has a lower sale price', async () => {
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 1,
      flash_sales: [
        {
          id: 9,
          item_id: 'item_unknown_discounted',
          item_price: 25,
          sale_price: 20,
          stock_limit: 12,
          stock_remaining: 8,
          status: 'active',
          starts_at: '2026-06-01T17:04:35+08:00',
          ends_at: '2026-06-09T17:04:35+08:00',
        },
      ],
    })

    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.text()).toContain('Now $20.00')
    expect(wrapper.text()).toContain('$25.00')
    expect(wrapper.text()).not.toContain('$27.00')
  })

  it('does not invent an original price when the backend does not return item_price', async () => {
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 1,
      flash_sales: [
        {
          id: 10,
          item_id: 'item_milk_pure',
          sale_price: 12.9,
          stock_limit: 12,
          stock_remaining: 8,
          status: 'active',
          starts_at: '2026-06-01T17:04:35+08:00',
          ends_at: '2026-06-09T17:04:35+08:00',
        },
      ],
    })

    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.text()).toContain('Now $12.90')
    expect(wrapper.text()).not.toContain('$18.40')
  })

  it('shows the first actionable departments and routes Electronics to its category page', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    await wrapper.get('[data-testid="store-header-departments-button"]').trigger('click')

    expect(wrapper.text()).toContain('Grocery')
    expect(wrapper.text()).toContain('Clothing, Shoes & Accessories')
    expect(wrapper.text()).toContain('Baby & Kids')
    expect(wrapper.text()).toContain('Electronics')

    await wrapper.get('[data-testid="store-header-department-electronics"]').trigger('click')

    expect(routerPush).toHaveBeenCalledWith('/cp/electronics')
  })

  it('loads flash sales on page render and purchases with default address', async () => {
    purchaseFlashSaleWithDefaultAddress.mockResolvedValue({
      ok: true,
      claim: { flash_sale_id: 2, user_id: 1, status: 'ordered' },
      order: { order_id: 'ORD-CODEX-1001', status: 'pending_fulfillment_review' },
      items: [],
    })
    const wrapper = mount(HomeView)

    await flushPromises()
    await wrapper.get('button[aria-label="Buy Pure milk flash deal"]').trigger('click')
    await flushPromises()

    expect(fetchFlashSales).toHaveBeenCalledWith({ status: 'active', limit: 20 })
    expect(purchaseFlashSaleWithDefaultAddress).toHaveBeenCalledWith(2, 1)
    expect(wrapper.text()).toContain('Order created')
  })
})
