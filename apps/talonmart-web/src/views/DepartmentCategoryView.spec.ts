import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import DepartmentCategoryView from './DepartmentCategoryView.vue'

const route = ref({
  params: {
    departmentSlug: 'electronics',
  },
})

const routerPush = vi.fn()
const { searchProductsByCategory } = vi.hoisted(() => ({
  searchProductsByCategory: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: {
    props: ['to'],
    template: '<a :data-to="typeof to === \'string\' ? to : JSON.stringify(to)"><slot /></a>',
  },
  useRoute: () => route.value,
  useRouter: () => ({
    push: routerPush,
  }),
}))

vi.mock('@/services/searchApi', () => ({
  searchProductsByCategory,
}))

describe('DepartmentCategoryView', () => {
  beforeEach(() => {
    route.value = {
      params: {
        departmentSlug: 'electronics',
      },
    }
    searchProductsByCategory.mockReset()
    routerPush.mockReset()
  })

  it('loads products for the department slug from the category search API', async () => {
    searchProductsByCategory.mockResolvedValue({
      ok: true,
      query: '',
      category: 'electronics',
      count: 2,
      items: [
        {
          item_id: 'item_wireless_earbuds',
          item_name: 'Wireless Earbuds',
          brand: 'Talon Audio',
          spec: 'Bluetooth 5.3',
          category_id: 'electronics',
          price: 59.99,
          rating: { score: 4.7, count: 940 },
          balances: [],
        },
        {
          item_id: 'item_unreviewed_tablet',
          item_name: 'Tablet Without Reviews',
          brand: 'Talon Tech',
          spec: '10 inch display',
          category_id: 'electronics',
          price: 129.99,
          rating: null,
          balances: [],
        },
      ],
    })

    const wrapper = mount(DepartmentCategoryView)
    await flushPromises()

    expect(searchProductsByCategory).toHaveBeenCalledWith('electronics')
    expect(wrapper.text()).toContain('Electronics')
    expect(wrapper.text()).toContain('Wireless Earbuds')
    expect(wrapper.text()).toContain('Talon Audio')

    const productCard = wrapper.get('[data-testid="product-card-item_wireless_earbuds"]')
    expect(productCard.classes().some((className) => className === 'border')).toBe(false)
    expect(productCard.classes().some((className) => className.startsWith('border-'))).toBe(false)
    expect(wrapper.get('[data-testid="product-rating-item_wireless_earbuds"]').text()).toContain(
      '4.7',
    )
    expect(wrapper.text()).toContain('940 ratings')
    expect(wrapper.get('[data-testid="product-rating-item_unreviewed_tablet"]').text()).toContain(
      'No ratings yet',
    )
  })

  it('uses the shared storefront header with search, account, cart, and departments dropdown', async () => {
    searchProductsByCategory.mockResolvedValue({
      ok: true,
      query: '',
      category: 'electronics',
      count: 0,
      items: [],
    })

    const wrapper = mount(DepartmentCategoryView)
    await flushPromises()

    expect(wrapper.get('[data-testid="store-header-logo"]').text()).toContain('TM')
    expect(wrapper.get('[data-testid="store-header-pickup"]').text()).toContain(
      'Sacramento, 95829',
    )
    expect(wrapper.find('input[aria-label="Search products"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="store-header-account"]').text()).toContain('Account')
    expect(wrapper.get('[data-testid="store-header-cart"]').text()).toContain('Cart')
    expect(wrapper.text()).not.toContain('Orders')
    expect(wrapper.text()).not.toContain('Services')
    expect(wrapper.text()).not.toContain('Rollbacks')

    const departmentsButton = wrapper.get('[data-testid="store-header-departments-button"]')
    expect(departmentsButton.text()).toContain('Departments')
    expect(wrapper.find('[data-testid="store-header-departments-menu"]').exists()).toBe(false)

    await departmentsButton.trigger('click')

    expect(wrapper.find('[data-testid="store-header-departments-menu"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="store-header-department-grocery"]').text()).toContain(
      'Grocery',
    )

    await wrapper.get('[data-testid="store-header-department-grocery"]').trigger('click')

    expect(routerPush).toHaveBeenCalledWith('/cp/grocery')
    expect(
      wrapper.find('[data-testid="department-inline-link-clothing-shoes-accessories"]').exists(),
    ).toBe(
      false,
    )

    await wrapper.get('input[aria-label="Search products"]').setValue('milk')
    await wrapper.get('form[role="search"]').trigger('submit')

    expect(routerPush).toHaveBeenCalledWith({ name: 'search', query: { q: 'milk' } })
  })

  it('shows clear empty and error states for category browsing', async () => {
    searchProductsByCategory.mockResolvedValueOnce({
      ok: true,
      query: '',
      category: 'electronics',
      count: 0,
      items: [],
    })
    const emptyWrapper = mount(DepartmentCategoryView)
    await flushPromises()

    expect(emptyWrapper.text()).toContain('No products found in Electronics')

    searchProductsByCategory.mockRejectedValueOnce(new Error('Category API unavailable'))
    const errorWrapper = mount(DepartmentCategoryView)
    await flushPromises()

    expect(errorWrapper.text()).toContain('Department request failed')
    expect(errorWrapper.text()).toContain('Category API unavailable')
  })
})
