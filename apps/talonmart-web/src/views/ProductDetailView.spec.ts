import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProductDetailView from './ProductDetailView.vue'

const routerPush = vi.fn()
const { fetchProductDetail, addCartItem, fetchItemReviews, createItemReview, fetchFlashSales } = vi.hoisted(() => ({
  fetchProductDetail: vi.fn(),
  addCartItem: vi.fn(),
  fetchItemReviews: vi.fn(),
  createItemReview: vi.fn(),
  fetchFlashSales: vi.fn(),
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

vi.mock('@/services/productReviewApi', () => ({
  fetchItemReviews,
  createItemReview,
}))

vi.mock('@/services/flashSaleApi', () => ({
  fetchFlashSales,
}))

describe('ProductDetailView', () => {
  beforeEach(() => {
    routerPush.mockClear()
    addCartItem.mockReset()
    fetchItemReviews.mockReset()
    createItemReview.mockReset()
    fetchFlashSales.mockReset()
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
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 0,
      flash_sales: [],
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
    fetchItemReviews.mockResolvedValue({
      ok: true,
      item_id: 'item_milk_pure',
      count: 1,
      summary: { average_rating: 4.5, review_count: 2 },
      reviews: [
        {
          id: 2,
          item_id: 'item_milk_pure',
          user_id: 2,
          rating: 5,
          title: 'Family pack is convenient',
          content: 'The 1L multipack is easy to store and works well for breakfast.',
          created_at: '2026-06-01T10:00:00+08:00',
          updated_at: '2026-06-01T10:00:00+08:00',
        },
      ],
    })
    createItemReview.mockResolvedValue({
      ok: true,
      review: {
        id: 5,
        item_id: 'item_milk_pure',
        user_id: 1,
        rating: 5,
        title: 'Good value',
        content: 'Fresh taste and good price for a family pack.',
        created_at: '2026-06-03T10:00:00+08:00',
        updated_at: '2026-06-03T10:00:00+08:00',
      },
    })
  })

  it('shows the active flash sale price when the product has a matching discount', async () => {
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 1,
      flash_sales: [
        {
          id: 114,
          item_id: 'item_milk_pure',
          item_price: 18.4,
          sale_price: 12.9,
          stock_limit: 30,
          stock_remaining: 30,
          status: 'active',
          starts_at: '2026-06-16T12:51:23.042362+08:00',
          ends_at: '2026-06-24T12:51:23.042362+08:00',
        },
      ],
    })

    const wrapper = mount(ProductDetailView)
    await flushPromises()

    expect(fetchFlashSales).toHaveBeenCalledWith({ status: 'active', limit: 100 })
    expect(wrapper.text()).toContain('Now $12.90')
    expect(wrapper.text()).toContain('$18.40')
  })

  it('keeps the regular detail price when active flash sale data lacks item_price', async () => {
    fetchFlashSales.mockResolvedValue({
      ok: true,
      count: 1,
      flash_sales: [
        {
          id: 115,
          item_id: 'item_milk_pure',
          sale_price: 12.9,
          stock_limit: 30,
          stock_remaining: 30,
          status: 'active',
          starts_at: '2026-06-16T12:51:23.042362+08:00',
          ends_at: '2026-06-24T12:51:23.042362+08:00',
        },
      ],
    })

    const wrapper = mount(ProductDetailView)
    await flushPromises()

    expect(wrapper.text()).toContain('$18.40')
    expect(wrapper.text()).not.toContain('Now $12.90')
  })

  it('loads product detail, shows zoom preview on image hover, and adds the item to cart', async () => {
    const wrapper = mount(ProductDetailView)

    await flushPromises()

    expect(fetchProductDetail).toHaveBeenCalledWith('item_milk_pure')
    expect(fetchItemReviews).toHaveBeenCalledWith('item_milk_pure', { limit: 20, offset: 0 })
    expect(wrapper.text()).toContain('Pure milk 1L multipack')
    expect(wrapper.text()).toContain('Pure milk for everyday use')
    expect(wrapper.text()).toContain('Customer reviews')
    expect(wrapper.text()).toContain('Family pack is convenient')

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

  it('creates a product review and refreshes the review list', async () => {
    const wrapper = mount(ProductDetailView)

    await flushPromises()
    await wrapper.get('select[aria-label="Review rating"]').setValue('5')
    await wrapper.get('input[aria-label="Review title"]').setValue('Good value')
    await wrapper
      .get('textarea[aria-label="Review content"]')
      .setValue('Fresh taste and good price for a family pack.')
    await wrapper.get('form[data-testid="item-review-form"]').trigger('submit')
    await flushPromises()

    expect(createItemReview).toHaveBeenCalledWith('item_milk_pure', {
      user_id: 1,
      rating: 5,
      title: 'Good value',
      content: 'Fresh taste and good price for a family pack.',
    })
    expect(fetchItemReviews).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Review submitted')
  })
})
