import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet } = vi.hoisted(() => ({
  apiGet: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
  },
}))

describe('searchApi', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  it('searches products by keyword using the documented q parameter', async () => {
    const { searchProducts } = await import('@/services/searchApi')

    apiGet.mockResolvedValue({ data: { ok: true, query: 'milk', count: 0, items: [] } })

    await searchProducts('milk')

    expect(apiGet).toHaveBeenCalledWith('/search', { params: { q: 'milk' } })
  })

  it('searches products by department category without sending a keyword query', async () => {
    const { searchProductsByCategory } = await import('@/services/searchApi')

    apiGet.mockResolvedValue({
      data: { ok: true, query: '', category: 'electronics', count: 0, items: [] },
    })

    await searchProductsByCategory('electronics')

    expect(apiGet).toHaveBeenCalledWith('/search', { params: { category: 'electronics' } })
  })
})
