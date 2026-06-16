import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'

import SearchView from './SearchView.vue'

const route = reactive({
  query: {
    q: 'earbuds',
  } as Record<string, string>,
})

const routerPush = vi.fn()
const { addCartItem, fetchCart, removeCartItem, searchProducts } = vi.hoisted(() => ({
  addCartItem: vi.fn(),
  fetchCart: vi.fn(),
  removeCartItem: vi.fn(),
  searchProducts: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a><slot /></a>',
  },
  useRoute: () => route,
  useRouter: () => ({
    push: routerPush,
  }),
}))

vi.mock('@/services/cartApi', () => ({
  addCartItem,
  CART_USER_ID: 1,
  fetchCart,
  removeCartItem,
}))

vi.mock('@/services/searchApi', () => ({
  searchProducts,
}))

describe('SearchView', () => {
  beforeEach(() => {
    route.query.q = 'earbuds'
    routerPush.mockReset()
    addCartItem.mockReset()
    fetchCart.mockReset()
    removeCartItem.mockReset()
    searchProducts.mockReset()
    fetchCart.mockResolvedValue({ ok: true, user_id: 1, count: 0, items: [] })
  })

  it('loads products from the q route parameter and renders searchable result cards', async () => {
    searchProducts.mockResolvedValue({
      ok: true,
      query: 'earbuds',
      count: 2,
      items: [
        {
          item_id: 'item_wireless_earbuds',
          item_name: 'Wireless Noise Cancelling Earbuds',
          brand: 'Talon Audio',
          spec: 'Bluetooth 5.3, 28-hour case',
          category_id: 'electronics',
          price: 59.99,
          rating: { score: 4.6, count: 1280 },
          balances: [
            {
              id: 1,
              warehouse_id: 'wh_hk_1',
              item_id: 'item_wireless_earbuds',
              quantity_on_hand: 8,
              storage_status: 'available',
            },
          ],
        },
        {
          item_id: 'item_unreviewed_speaker',
          item_name: 'Portable Speaker Without Reviews',
          brand: 'Talon Audio',
          spec: 'Compact bluetooth speaker',
          category_id: 'electronics',
          price: 29.99,
          rating: null,
          balances: [],
        },
      ],
    })

    const wrapper = mount(SearchView)
    await flushPromises()

    expect(searchProducts).toHaveBeenCalledWith('earbuds')
    expect(wrapper.text()).toContain('Results for "earbuds"')
    expect(wrapper.text()).toContain('Wireless Noise Cancelling Earbuds')
    expect(wrapper.text()).toContain('8 units')

    const productCard = wrapper.get('[data-testid="product-card-item_wireless_earbuds"]')
    expect(productCard.classes().some((className) => className === 'border')).toBe(false)
    expect(productCard.classes().some((className) => className.startsWith('border-'))).toBe(false)
    expect(wrapper.get('[data-testid="product-rating-item_wireless_earbuds"]').text()).toContain(
      '4.6',
    )
    expect(wrapper.text()).toContain('1,280 ratings')
    expect(wrapper.get('[data-testid="product-rating-item_unreviewed_speaker"]').text()).toContain(
      'No ratings yet',
    )
  })

  it('shows clear empty and error states for search results', async () => {
    searchProducts.mockResolvedValueOnce({ ok: true, query: 'missing', count: 0, items: [] })
    const emptyWrapper = mount(SearchView)
    await flushPromises()

    expect(emptyWrapper.text()).toContain('No results found')

    searchProducts.mockRejectedValueOnce(new Error('Search API unavailable'))
    const errorWrapper = mount(SearchView)
    await flushPromises()

    expect(errorWrapper.text()).toContain('Search request failed')
    expect(errorWrapper.text()).toContain('Search API unavailable')
  })
})
