import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet } = vi.hoisted(() => ({
  apiGet: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
  },
}))

describe('categoryRankingApi', () => {
  beforeEach(() => {
    apiGet.mockReset()
  })

  it('fetches a category ranking using the documented ranking endpoint', async () => {
    const { fetchCategoryRanking } = await import('@/services/categoryRankingApi')

    apiGet.mockResolvedValue({
      data: {
        ok: true,
        category_id: 'electronics',
        rank_type: 'hot',
        window_type: 'all_time',
        count: 1,
        items: [{ rank: 1, item_id: 'item_wireless_earbuds', score: 92 }],
      },
    })

    const response = await fetchCategoryRanking('electronics', { limit: 3 })

    expect(apiGet).toHaveBeenCalledWith('/rankings/categories/electronics', {
      params: { limit: 3 },
    })
    expect(response.items.at(0)?.item_id).toBe('item_wireless_earbuds')
  })

  it('fetches homepage hot rankings for the recommendation rail', async () => {
    const { fetchHomeHotRankings } = await import('@/services/categoryRankingApi')

    apiGet.mockResolvedValue({
      data: {
        ok: true,
        rank_type: 'hot',
        window_type: 'all_time',
        count: 1,
        items: [{ rank: 1, item_id: 'item_milk_pure', score: 88 }],
      },
    })

    const response = await fetchHomeHotRankings({ limit: 8 })

    expect(apiGet).toHaveBeenCalledWith('/rankings/home/hot', {
      params: { limit: 8 },
    })
    expect(response.items).toHaveLength(1)
  })
})
