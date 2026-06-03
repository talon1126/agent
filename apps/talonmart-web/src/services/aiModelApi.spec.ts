import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('aiModelApi', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.unstubAllGlobals()
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
            'event: done\ndata: {"conversation_id":"conv_1","answer":"推荐减压魔方。","recommended_links":[{"item_id":"item_toy_cube","item_name":"减压魔方","url":"/items/item_toy_cube"}]}\n\n',
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
        conversation_id: 'conv_1',
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
        conversation_id: 'conv_1',
        message: '有推荐的解压玩具吗',
        links: ['https://shop.example.com/items/item_toy_cube'],
      }),
    })
    expect(statuses).toEqual(['正在理解问题'])
    expect(deltas).toEqual(['推荐', '减压魔方。'])
    expect(response.answer).toBe('推荐减压魔方。')
    expect(response.recommended_links).toEqual([
      { item_id: 'item_toy_cube', item_name: '减压魔方', url: '/items/item_toy_cube' },
    ])
    expect('tool_results' in response).toBe(false)
  })
})
