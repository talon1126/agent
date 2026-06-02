import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost, fetchDeliveryAddresses } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  fetchDeliveryAddresses: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
    post: apiPost,
  },
}))

vi.mock('@/services/checkoutApi', () => ({
  fetchDeliveryAddresses,
}))

describe('flashSaleApi', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
    fetchDeliveryAddresses.mockReset()
  })

  it('purchases a flash sale with the default delivery address', async () => {
    const { purchaseFlashSaleWithDefaultAddress } = await import('@/services/flashSaleApi')

    fetchDeliveryAddresses.mockResolvedValue({
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
    })
    apiPost.mockResolvedValue({
      data: {
        ok: true,
        claim: { flash_sale_id: 3, user_id: 1, status: 'ordered' },
        order: { order_id: 'ORD-CODEX-1001', status: '未付款' },
        items: [],
      },
    })

    await purchaseFlashSaleWithDefaultAddress(3, 1)

    expect(fetchDeliveryAddresses).toHaveBeenCalledWith(1)
    expect(apiPost).toHaveBeenCalledWith('/flash-sales/3/purchase', {
      user_id: 1,
      shipping_address: '广东省深圳市南山区示例路 100 号',
      delivery_provider_id: 'sf',
    })
  })
})
