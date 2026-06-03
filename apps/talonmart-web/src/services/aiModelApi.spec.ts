import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('aiModelApi', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('streams AImodel chat events from the existing chat route', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: status\ndata: {"content":"正在理解问题"}\n\n'))
        controller.enqueue(encoder.encode('event: delta\ndata: {"content":"推荐"}\n\n'))
        controller.enqueue(encoder.encode('event: delta\ndata: {"content":"减压魔方。"}\n\n'))
        controller.enqueue(
          encoder.encode(
            'event: done\ndata: {"conversation_id":123,"answer":"推荐减压魔方。","recommended_links":[{"item_id":"item_toy_cube","item_name":"减压魔方","url":"/items/item_toy_cube"}]}\n\n',
          ),
        )
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: stream,
    })
    vi.stubGlobal('fetch', fetchMock)
    const { streamAiModel } = await import('@/services/aiModelApi')

    const statuses: string[] = []
    const deltas: string[] = []

    const response = await streamAiModel(
      {
        conversation_id: null,
        user_id: 'anon_test',
        message: '有推荐的解压玩具吗',
        links: ['https://shop.example.com/items/item_toy_cube'],
      },
      {
        onStatus: (content) => statuses.push(content),
        onDelta: (content) => deltas.push(content),
      },
    )

    expect(fetchMock).toHaveBeenCalledWith('/ai-service/AImodel/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: null,
        user_id: 'anon_test',
        message: '有推荐的解压玩具吗',
        links: ['https://shop.example.com/items/item_toy_cube'],
      }),
    })
    expect(statuses).toEqual(['正在理解问题'])
    expect(deltas).toEqual(['推荐', '减压魔方。'])
    expect(response.answer).toBe('推荐减压魔方。')
    expect(response.conversation_id).toBe(123)
    expect(response.recommended_links).toEqual([
      { item_id: 'item_toy_cube', item_name: '减压魔方', url: '/items/item_toy_cube' },
    ])
    expect('tool_results' in response).toBe(false)
  })

  it('creates and reuses a local anonymous AImodel user id', async () => {
    const randomUUID = vi.fn().mockReturnValue('00000000-0000-4000-8000-000000000001')
    vi.stubGlobal('crypto', { randomUUID })
    const { getOrCreateAiModelUserId } = await import('@/services/aiModelApi')

    const firstUserId = getOrCreateAiModelUserId()
    const secondUserId = getOrCreateAiModelUserId()

    expect(firstUserId).toBe('anon_00000000-0000-4000-8000-000000000001')
    expect(secondUserId).toBe(firstUserId)
    expect(localStorage.getItem('aimodel_user_id')).toBe(firstUserId)
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })
})
