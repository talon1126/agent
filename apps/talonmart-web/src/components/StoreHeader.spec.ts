import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import StoreHeader from './StoreHeader.vue'

const routerPush = vi.fn()

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a :data-to="typeof to === \'string\' ? to : JSON.stringify(to)"><slot /></a>',
  },
  useRouter: () => ({
    push: routerPush,
  }),
}))

describe('StoreHeader', () => {
  it('renders the shared storefront header without the removed shortcut tabs', async () => {
    routerPush.mockReset()

    const wrapper = mount(StoreHeader, {
      props: {
        initialSearchQuery: 'milk',
        cartQuantity: 2,
      },
    })

    expect(wrapper.get('[data-testid="store-header-logo"]').text()).toContain('TM')
    expect(wrapper.find('[data-testid="store-header-pickup"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Pickup or delivery?')
    expect(wrapper.text()).not.toContain('Sacramento, 95829')
    expect(wrapper.find('input[aria-label="Search products"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="store-header-account"]').text()).toContain('Account')
    expect(wrapper.get('[data-testid="store-header-cart"]').text()).toContain('Cart')
    expect(wrapper.get('[data-testid="store-header-cart"]').text()).toContain('2')
    expect(wrapper.get('[data-testid="store-header-departments-button"]').text()).toContain(
      'Departments',
    )

    expect(wrapper.text()).not.toContain('Services')
    expect(wrapper.text()).not.toContain('Rollbacks')
    expect(wrapper.text()).not.toContain("Father's Day")
    expect(wrapper.text()).not.toContain('Get it Fast')
    expect(wrapper.text()).not.toContain('Pharmacy')
    expect(wrapper.text()).not.toContain('New Arrivals')
    expect(wrapper.text()).not.toContain('TalonMart+')

    await wrapper.get('[data-testid="store-header-departments-button"]').trigger('click')
    await wrapper.get('[data-testid="store-header-department-electronics"]').trigger('click')

    expect(routerPush).toHaveBeenCalledWith('/cp/electronics')

    await wrapper.get('input[aria-label="Search products"]').setValue('earbuds')
    await wrapper.get('form[role="search"]').trigger('submit')

    expect(routerPush).toHaveBeenCalledWith({ name: 'search', query: { q: 'earbuds' } })
  })
})
