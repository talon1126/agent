import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HomeView from './HomeView.vue'

const routerPush = vi.fn()
const { fetchFlashSales, purchaseFlashSaleWithDefaultAddress } = vi.hoisted(() => ({
  fetchFlashSales: vi.fn(),
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
  })

  it('renders the TalonMart storefront essentials', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    expect(wrapper.text()).toContain('TalonMart')
    expect(wrapper.get('input[aria-label="Search products"]').attributes('placeholder')).toContain(
      'Search',
    )
    expect(wrapper.text()).toContain('Paper Goods')
    expect(wrapper.text()).toContain('Today deals')
    expect(wrapper.text()).toContain('Flash Deals')
    expect(wrapper.text()).toContain('Pure milk flash deal')
    expect(wrapper.text()).toContain('30 left')
    expect(wrapper.text()).toContain('Cart')
  })

  it('loads flash sales on page render and purchases with default address', async () => {
    purchaseFlashSaleWithDefaultAddress.mockResolvedValue({
      ok: true,
      claim: { flash_sale_id: 2, user_id: 1, status: 'ordered' },
      order: { order_id: 'ORD-CODEX-1001', status: '未付款' },
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
