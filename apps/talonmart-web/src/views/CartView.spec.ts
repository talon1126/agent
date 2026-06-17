import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CartView from './CartView.vue'

const routerPush = vi.fn()
const { apiGet, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
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

vi.mock('@/services/cartApi', () => ({
  CART_USER_ID: 1,
  fetchCart: vi.fn(async () => ({
    ok: true,
    user_id: 1,
    count: 1,
    items: [
      {
        id: 10,
        user_id: 1,
        item_id: 'item_milk_pure',
        item_name: 'Pure Milk',
        price: 16.19,
        quantity: 2,
      },
    ],
  })),
  addCartItem: vi.fn(),
  removeCartItem: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
    post: apiPost,
  },
}))

describe('CartView checkout', () => {
  beforeEach(() => {
    routerPush.mockClear()
    apiGet.mockResolvedValue({
      data: {
        ok: true,
        user_id: 1,
        count: 1,
        items: [
          {
            id: 1,
            user_id: 1,
            receiver_name: 'Talon 测试用户',
            phone_number: '13800000001',
            address: '广东省深圳市南山区示例路 100 号',
            is_default: 1,
          },
        ],
      },
    })
    apiPost.mockResolvedValue({
      data: {
        ok: true,
        order: {
          order_id: 'ORD-CODEX-1001',
          customer_id: '1',
          status: 'unpaid',
        },
        items: [],
      },
    })
  })

  it('shows the default delivery address and displays unpaid status after checkout', async () => {
    const wrapper = mount(CartView)

    await flushPromises()

    expect(wrapper.text()).toContain('Talon 测试用户')
    expect(wrapper.text()).toContain('广东省深圳市南山区示例路 100 号')

    await wrapper.get('button[aria-label="Continue to checkout"]').trigger('click')
    await flushPromises()

    expect(apiGet).toHaveBeenCalledWith('/delivery_addresses', {
      params: { user_id: 1 },
    })
    expect(apiPost).toHaveBeenCalledWith('/warehouse/orders', {
      customer_id: '1',
      delivery_provider_id: 'sf',
      courier_phone: '',
      shipping_address: '广东省深圳市南山区示例路 100 号',
      items: [{ item_id: 'item_milk_pure', quantity: 2 }],
      created_by: 'talonmart-web',
    })
    expect(routerPush).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Order ORD-CODEX-1001 was created and is waiting for payment.')
  })
})
