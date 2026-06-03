import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AiModeSidebar from './AiModeSidebar.vue'

const { askAiModel } = vi.hoisted(() => ({
  askAiModel: vi.fn(),
}))

vi.mock('@/services/aiModelApi', () => ({
  askAiModel,
}))

describe('AiModeSidebar', () => {
  beforeEach(() => {
    askAiModel.mockReset()
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
    askAiModel.mockResolvedValue({
      conversation_id: 'conv_1',
      answer: '推荐先看减压魔方，并比较材质和尺寸。',
      recommended_links: [{ item_id: 'item_toy_cube', item_name: '减压魔方', url: '/items/item_toy_cube' }],
      tool_results: [],
    })
    const wrapper = mount(AiModeSidebar)

    await wrapper.get('button[aria-label="Open AI mode"]').trigger('click')
    await wrapper.get('button[data-testid="ai-quick-prompt"]').trigger('click')
    await flushPromises()

    expect(askAiModel).toHaveBeenCalledWith({
      conversation_id: expect.any(String),
      message: '居家提升幸福感好物',
      links: [],
    })
    expect(wrapper.text()).toContain('推荐先看减压魔方')
    expect(wrapper.text()).toContain('减压魔方')
  })
})
