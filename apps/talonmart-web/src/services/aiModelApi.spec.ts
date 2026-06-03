import { beforeEach, describe, expect, it, vi } from 'vitest'

const { axiosCreate, aiServicePost } = vi.hoisted(() => ({
  axiosCreate: vi.fn(),
  aiServicePost: vi.fn(),
}))

vi.mock('axios', () => ({
  default: {
    create: axiosCreate,
  },
}))

describe('aiModelApi', () => {
  beforeEach(() => {
    vi.resetModules()
    aiServicePost.mockReset()
    axiosCreate.mockReset()
    axiosCreate.mockReturnValue({
      post: aiServicePost,
    })
  })

  it('creates a dedicated ai-service client instead of using the mock-api client', async () => {
    const { askAiModel } = await import('@/services/aiModelApi')

    aiServicePost.mockResolvedValue({
      data: {
        conversation_id: 'conv_1',
        answer: '可以优先选择减压魔方。',
        recommended_links: [{ item_id: 'item_toy_cube', item_name: '减压魔方', url: '/items/item_toy_cube' }],
        tool_results: [],
      },
    })

    const response = await askAiModel({
      conversation_id: 'conv_1',
      message: '有推荐的解压玩具吗',
      links: ['https://shop.example.com/items/item_toy_cube'],
    })

    expect(axiosCreate).toHaveBeenCalledWith({
      baseURL: '/ai-service',
      timeout: 30_000,
    })
    expect(aiServicePost).toHaveBeenCalledWith('/AImodel/chat', {
      conversation_id: 'conv_1',
      message: '有推荐的解压玩具吗',
      links: ['https://shop.example.com/items/item_toy_cube'],
    })
    expect(response.answer).toBe('可以优先选择减压魔方。')
  })
})
