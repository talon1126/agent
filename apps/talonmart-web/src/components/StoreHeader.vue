<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ChevronDown, MapPin, Menu, Search, ShoppingCart, UserRound } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    initialSearchQuery?: string
    cartQuantity?: number
  }>(),
  {
    initialSearchQuery: '',
    cartQuantity: 0,
  },
)

const router = useRouter()

const departmentMenuItems = [
  { label: 'All Departments' },
  { label: 'Grocery', slug: 'grocery' },
  { label: 'Clothing, Shoes & Accessories', slug: 'clothing-shoes-accessories' },
  { label: 'Baby & Kids', slug: 'baby-kids' },
  { label: 'Electronics', slug: 'electronics' },
]

const searchQuery = ref(props.initialSearchQuery)
const isDepartmentMenuOpen = ref(false)

watch(
  () => props.initialSearchQuery,
  (value) => {
    searchQuery.value = value
  },
)

function submitSearch() {
  const normalized = searchQuery.value.trim()
  if (!normalized) return
  router.push({ name: 'search', query: { q: normalized } })
}

function openDepartment(slug?: string) {
  if (!slug) return
  isDepartmentMenuOpen.value = false
  router.push(`/cp/${slug}`)
}
</script>

<template>
  <header class="sticky top-0 z-30 bg-[#0053E2] text-white shadow-sm">
    <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-6 py-3">
      <RouterLink
        class="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-[#FFC220] text-lg font-black text-white"
        to="/"
        aria-label="TalonMart home"
        data-testid="store-header-logo"
      >
        TM
      </RouterLink>

      <button
        class="hidden min-h-12 shrink-0 items-center gap-3 rounded-full bg-[#003A9B] px-5 text-left text-sm font-semibold xl:flex"
        type="button"
        data-testid="store-header-pickup"
      >
        <MapPin class="h-6 w-6 text-[#FFC220]" aria-hidden="true" />
        <span>
          <span class="block text-sm leading-5 text-white">Pickup or delivery?</span>
          <span class="block max-w-[220px] truncate text-sm leading-5 text-white/90">
            Sacramento, 95829
          </span>
        </span>
        <ChevronDown class="h-4 w-4" aria-hidden="true" />
      </button>

      <form
        class="flex min-h-12 flex-1 overflow-hidden rounded-full bg-white"
        role="search"
        @submit.prevent="submitSearch"
      >
        <input
          v-model="searchQuery"
          aria-label="Search products"
          class="min-w-0 flex-1 px-6 text-base text-[#101828] outline-none placeholder:text-[#0053A6]"
          placeholder="Search everything at TalonMart online and in store"
          type="search"
        />
        <button
          class="grid w-14 place-items-center bg-[#003A9B] text-white transition hover:bg-[#002F7D]"
          type="submit"
          aria-label="Submit search"
        >
          <Search class="h-6 w-6" aria-hidden="true" />
        </button>
      </form>

      <RouterLink
        class="hidden min-h-11 shrink-0 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex"
        to="/"
        data-testid="store-header-account"
      >
        <UserRound class="h-5 w-5" aria-hidden="true" />
        Account
      </RouterLink>
      <RouterLink
        class="relative flex min-h-11 shrink-0 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10"
        to="/cart"
        data-testid="store-header-cart"
      >
        <ShoppingCart class="h-6 w-6" aria-hidden="true" />
        Cart
        <span
          v-if="cartQuantity"
          class="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-[#FFC220] px-1 text-xs font-black text-[#101828]"
        >
          {{ cartQuantity }}
        </span>
      </RouterLink>
    </div>

    <nav class="border-b border-[#D8E0E8] bg-[#F3F8FF] text-[#101828]">
      <div class="mx-auto flex max-w-[1440px] px-6 py-2">
        <div class="relative">
          <button
            class="flex min-h-10 items-center gap-2 rounded-full bg-white px-4 text-sm font-bold shadow-sm transition hover:bg-[#EAF2FF]"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="isDepartmentMenuOpen"
            data-testid="store-header-departments-button"
            @click="isDepartmentMenuOpen = !isDepartmentMenuOpen"
          >
            <Menu class="h-4 w-4" aria-hidden="true" />
            Departments
            <ChevronDown class="h-4 w-4" aria-hidden="true" />
          </button>

          <div
            v-if="isDepartmentMenuOpen"
            class="absolute left-0 top-full z-40 mt-2 w-[300px] overflow-hidden rounded-md border border-[#D8E0E8] bg-white py-2 text-[#101828] shadow-xl"
            role="menu"
            data-testid="store-header-departments-menu"
          >
            <button
              v-for="item in departmentMenuItems"
              :key="item.label"
              class="flex min-h-11 w-full items-center justify-between px-4 text-left text-sm font-semibold hover:bg-[#EAF2FF] disabled:cursor-default disabled:text-[#98A2B3] disabled:hover:bg-white"
              type="button"
              role="menuitem"
              :disabled="!item.slug"
              :data-testid="item.slug ? `store-header-department-${item.slug}` : undefined"
              @click="openDepartment(item.slug)"
            >
              <span>{{ item.label }}</span>
              <span v-if="item.slug" class="text-xs font-bold text-[#0053E2]">Shop</span>
            </button>
          </div>
        </div>
      </div>
    </nav>
  </header>
</template>
