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
        order: { order_id: 'ORD-CODEX-1001', status: 'pending_fulfillment_review' },
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

  it('uses backend business message when flash sale purchase is rejected', async () => {
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
    const axiosError = Object.assign(new Error('Request failed with status code 409'), {
      isAxiosError: true,
      response: {
        data: {
          ok: false,
          error: 'purchase_limit_reached',
          message: '已达到购买上限',
        },
      },
    })
    apiPost.mockRejectedValue(axiosError)

    await expect(purchaseFlashSaleWithDefaultAddress(3, 1)).rejects.toThrow('已达到购买上限')
  })

  it('fetches active flash sale list for the storefront section', async () => {
    const { fetchFlashSales } = await import('@/services/flashSaleApi')

    apiGet.mockResolvedValue({
      data: {
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
      },
    })

    const response = await fetchFlashSales({ status: 'active', limit: 20 })

    expect(apiGet).toHaveBeenCalledWith('/flash-sales', {
      params: { status: 'active', limit: 20 },
    })
    expect(response.flash_sales).toEqual([expect.objectContaining({ item_id: 'item_milk_pure' })])
  })
})
