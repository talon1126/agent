import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import HomeView from './HomeView.vue'

describe('HomeView', () => {
  it('renders the TalonMart storefront essentials', () => {
    const wrapper = mount(HomeView)

    expect(wrapper.text()).toContain('TalonMart')
    expect(wrapper.get('input[aria-label="Search products"]').attributes('placeholder')).toContain(
      'Search',
    )
    expect(wrapper.text()).toContain('Paper Goods')
    expect(wrapper.text()).toContain('Today deals')
    expect(wrapper.text()).toContain('Flash Deals')
    expect(wrapper.text()).toContain('Waiting for the flash sale list API')
    expect(wrapper.text()).toContain('Cart')
  })
})
