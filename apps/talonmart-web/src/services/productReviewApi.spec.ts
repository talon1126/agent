import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

vi.mock('@/services/apiClient', () => ({
  apiClient: {
    get: apiGet,
    post: apiPost,
  },
}))

describe('productReviewApi', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  it('fetches item reviews by item id', async () => {
    const { fetchItemReviews } = await import('@/services/productReviewApi')

    apiGet.mockResolvedValue({
      data: {
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
            content: 'The 1L multipack is easy to store.',
            created_at: '2026-06-01T10:00:00+08:00',
            updated_at: '2026-06-01T10:00:00+08:00',
          },
        ],
      },
    })

    const response = await fetchItemReviews('item_milk_pure', { limit: 20, offset: 0 })

    expect(apiGet).toHaveBeenCalledWith('/items/item_milk_pure/reviews', {
      params: { limit: 20, offset: 0 },
    })
    expect(response.summary.average_rating).toBe(4.5)
  })

  it('creates an item review', async () => {
    const { createItemReview } = await import('@/services/productReviewApi')

    apiPost.mockResolvedValue({
      data: {
        ok: true,
        review: {
          id: 5,
          item_id: 'item_milk_pure',
          user_id: 1,
          rating: 5,
          title: 'Good value',
          content: 'Fresh taste and good price.',
          created_at: '2026-06-03T10:00:00+08:00',
          updated_at: '2026-06-03T10:00:00+08:00',
        },
      },
    })

    await createItemReview('item_milk_pure', {
      user_id: 1,
      rating: 5,
      title: 'Good value',
      content: 'Fresh taste and good price.',
    })

    expect(apiPost).toHaveBeenCalledWith('/items/item_milk_pure/reviews', {
      user_id: 1,
      rating: 5,
      title: 'Good value',
      content: 'Fresh taste and good price.',
    })
  })
})
