import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AiModeSidebar from './AiModeSidebar.vue'

const { getOrCreateAiModelUserId, streamAiModel } = vi.hoisted(() => ({
  getOrCreateAiModelUserId: vi.fn(),
  streamAiModel: vi.fn(),
}))

vi.mock('@/services/aiModelApi', () => ({
  getOrCreateAiModelUserId,
  streamAiModel,
}))

describe('AiModeSidebar', () => {
  beforeEach(() => {
    streamAiModel.mockReset()
    getOrCreateAiModelUserId.mockReset()
    getOrCreateAiModelUserId.mockReturnValue('anon_component_test')
    localStorage.clear()
  })

  it('opens and closes the AI mode chat panel from the sidebar entry', async () => {
    const wrapper = mount(AiModeSidebar)

    expect(wrapper.text()).toContain('AI模式')
    expect(wrapper.text()).not.toContain('购物车')
    expect(wrapper.text()).not.toContain('桌面版')
    expect(wrapper.text()).not.toContain('插件版')
    expect(wrapper.text()).not.toContain('有问题，找京言')

    await wrapper.get('button[aria-label="Open AI mode"]').trigger('click')
    expect(wrapper.text()).toContain('有问题，找京言')
    expect(wrapper.find('textarea[aria-label="请输入你的问题"]').exists()).toBe(true)

    await wrapper.get('button[aria-label="Close AI mode"]').trigger('click')
    expect(wrapper.text()).not.toContain('有问题，找京言')
  })

  it('sends a quick prompt through AImodel and renders the answer', async () => {
    let emitFirstDelta: (() => void) | undefined
    let finishStream: (() => void) | undefined
    streamAiModel.mockImplementation(async (_request, handlers) => {
      handlers.onStatus('正在生成回答')
      return await new Promise((resolve) => {
        emitFirstDelta = () => {
          handlers.onDelta('推荐先看')
        }
        finishStream = () => {
          handlers.onDelta('减压魔方，并比较材质和尺寸。')
          const response = {
            conversation_id: 123,
            answer: '推荐先看减压魔方，并比较材质和尺寸。',
            recommended_links: [{ item_id: 'item_toy_cube', item_name: '减压魔方', url: '/items/item_toy_cube' }],
          }
          handlers.onDone(response)
          resolve(response)
        }
      })
    })
    const wrapper = mount(AiModeSidebar)

    await wrapper.get('button[aria-label="Open AI mode"]').trigger('click')
    await wrapper.get('button[data-testid="ai-quick-prompt"]').trigger('click')
    await flushPromises()

    expect(streamAiModel).toHaveBeenCalledWith(
      {
        user_id: 'anon_component_test',
        conversation_id: null,
        message: '居家提升幸福感好物',
        links: [],
      },
      expect.objectContaining({
        onStatus: expect.any(Function),
        onDelta: expect.any(Function),
        onDone: expect.any(Function),
      }),
    )
    expect(wrapper.text()).not.toContain('推荐先看')

    emitFirstDelta?.()
    await flushPromises()

    expect(wrapper.text()).toContain('推荐先看')
    expect(wrapper.text()).not.toContain('减压魔方，并比较材质和尺寸。')

    finishStream?.()
    await flushPromises()

    expect(wrapper.text()).toContain('推荐先看减压魔方')
    expect(wrapper.text()).toContain('减压魔方')
  })

  it('formats assistant answers into readable paragraphs and list items', async () => {
    streamAiModel.mockImplementation(async (_request, handlers) => {
      const response = {
        conversation_id: 123,
        answer: '推荐这两类：\n\n- 减压魔方：适合桌面把玩。\n- 指尖陀螺：适合短时间放松。',
        recommended_links: [],
      }
      handlers.onDelta(response.answer)
      handlers.onDone(response)
      return response
    })
    const wrapper = mount(AiModeSidebar)

    await wrapper.get('button[aria-label="Open AI mode"]').trigger('click')
    await wrapper.get('button[data-testid="ai-quick-prompt"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.ai-message__paragraph')).toHaveLength(1)
    expect(wrapper.findAll('.ai-message__list-item')).toHaveLength(2)
  })
})
