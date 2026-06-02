import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet } = vi.hoisted(() => ({
  apiGet: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
  },
}))

describe('productDetailApi', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  it('fetches product detail by item id from the documented endpoint', async () => {
    const { fetchProductDetail } = await import('@/services/productDetailApi')

    apiGet.mockResolvedValue({
      data: {
        ok: true,
        item: {
          item_id: 'item_milk_pure',
          item_name: 'Pure Milk',
          brand: 'Talon Value',
          spec: '1L x 6',
          category_id: 'dairy',
          price: 18.4,
        },
      },
    })

    const response = await fetchProductDetail('item_milk_pure')

    expect(apiGet).toHaveBeenCalledWith('/ip/item_milk_pure')
    expect(response.item.item_id).toBe('item_milk_pure')
  })
})
