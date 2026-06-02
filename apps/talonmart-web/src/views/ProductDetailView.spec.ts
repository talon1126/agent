import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProductDetailView from './ProductDetailView.vue'

const routerPush = vi.fn()
const { fetchProductDetail, addCartItem } = vi.hoisted(() => ({
  fetchProductDetail: vi.fn(),
  addCartItem: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a><slot /></a>',
  },
  useRoute: () => ({
    params: { item_id: 'item_milk_pure' },
  }),
  useRouter: () => ({
    push: routerPush,
  }),
}))

vi.mock('@/services/productDetailApi', () => ({
  fetchProductDetail,
}))

vi.mock('@/services/cartApi', () => ({
  CART_USER_ID: 1,
  addCartItem,
}))

describe('ProductDetailView', () => {
  beforeEach(() => {
    routerPush.mockClear()
    addCartItem.mockReset()
    fetchProductDetail.mockResolvedValue({
      ok: true,
      item: {
        item_id: 'item_milk_pure',
        item_name: 'Pure milk 1L multipack',
        brand: 'Talon Value',
        spec: '1L x 6',
        category_id: 'dairy',
        price: 18.4,
        currency: 'USD',
        images: [
          {
            url: 'https://example.com/milk-front.png',
            alt: 'Pure milk front view',
            sort_order: 1,
          },
        ],
        rating: { score: 4.6, count: 1280 },
        badges: ['Overall pick'],
        features: ['Pure milk for everyday use'],
        ingredients: 'Milk.',
        description: 'Fresh pure milk for daily household needs.',
        details: [{ label: 'Flavor', value: 'Original' }],
        fulfillment: {
          shipping_available: false,
          pickup_available: true,
          delivery_available: true,
          pickup_message: 'As soon as today',
          delivery_message: 'As soon as tomorrow',
        },
      },
    })
    addCartItem.mockResolvedValue({
      ok: true,
      item: {
        id: 1,
        user_id: 1,
        item_id: 'item_milk_pure',
        item_name: 'Pure milk 1L multipack',
        price: 18.4,
        quantity: 1,
      },
    })
  })

  it('loads product detail, shows zoom preview on image hover, and adds the item to cart', async () => {
    const wrapper = mount(ProductDetailView)

    await flushPromises()

    expect(fetchProductDetail).toHaveBeenCalledWith('item_milk_pure')
    expect(wrapper.text()).toContain('Pure milk 1L multipack')
    expect(wrapper.text()).toContain('Pure milk for everyday use')

    await wrapper.get('[data-testid="product-main-image"]').trigger('mouseenter')
    await wrapper.get('[data-testid="product-main-image"]').trigger('mousemove', {
      clientX: 180,
      clientY: 160,
    })

    expect(wrapper.find('[data-testid="product-zoom-preview"]').exists()).toBe(true)

    await wrapper.get('button[aria-label="Add Pure milk 1L multipack to cart"]').trigger('click')
    await flushPromises()

    expect(addCartItem).toHaveBeenCalledWith({
      user_id: 1,
      item_id: 'item_milk_pure',
      item_name: 'Pure milk 1L multipack',
      price: 18.4,
      quantity: 1,
    })
    expect(wrapper.text()).toContain('Added to cart')
  })
})
