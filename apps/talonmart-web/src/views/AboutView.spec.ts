import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AboutView from './AboutView.vue'

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a><slot /></a>',
  },
  useRouter: () => ({
    push: vi.fn(),
  }),
}))

describe('AboutView', () => {
  it('uses the shared storefront header without shortcut tabs', () => {
    const wrapper = mount(AboutView)

    expect(wrapper.get('[data-testid="store-header-logo"]').text()).toContain('TM')
    expect(wrapper.get('[data-testid="store-header-departments-button"]').text()).toContain(
      'Departments',
    )
    expect(wrapper.text()).not.toContain('Services')
    expect(wrapper.text()).not.toContain('Rollbacks')
  })
})
