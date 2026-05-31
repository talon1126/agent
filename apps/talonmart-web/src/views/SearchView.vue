<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BadgeDollarSign,
  ChevronDown,
  ClipboardList,
  Home,
  LoaderCircle,
  MapPin,
  Menu,
  RefreshCw,
  Search,
  ShoppingCart,
  SlidersHorizontal,
  Truck,
  UserRound,
} from 'lucide-vue-next'

import { searchProducts } from '@/services/searchApi'
import type { SearchProduct } from '@/types/search'

const route = useRoute()
const router = useRouter()

const searchQuery = ref(String(route.query.q ?? ''))
const searchedQuery = ref('')
const products = ref<SearchProduct[]>([])
const isLoading = ref(false)
const errorMessage = ref('')

const topTabs = ['Departments', 'Services', 'Rollbacks & More', 'Fast delivery', 'Fresh food']
const quickFilters = ['In-store', 'Get it fast', 'All deals', 'Price', 'Brand', 'Subscription']
const departments = ['Dairy', 'Beverages', 'Paper Goods', 'Office Supplies']

const milkShortcuts = [
  { label: 'Shop all milk', image: '' },
  {
    label: 'Whole milk',
    image:
      'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=240&q=80',
  },
  {
    label: '2% milk',
    image:
      'https://images.unsplash.com/photo-1600788907416-456578634209?auto=format&fit=crop&w=240&q=80',
  },
  {
    label: 'Organic milk',
    image:
      'https://images.unsplash.com/photo-1568649929103-28ffbefaca1e?auto=format&fit=crop&w=240&q=80',
  },
]

const resultCountLabel = computed(() =>
  products.value.length === 1 ? '1 result' : `${products.value.length} results`,
)

function totalStock(product: SearchProduct) {
  return product.balances.reduce((total, balance) => total + balance.quantity_on_hand, 0)
}

function stockLabel(product: SearchProduct) {
  const total = totalStock(product)
  if (total <= 0) return 'Out of stock'
  if (total < 50) return 'Low stock'
  return 'In stock'
}

function productImage(product: SearchProduct) {
  if (product.item_id.includes('milk')) {
    return 'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=640&q=80'
  }
  if (product.item_id.includes('cola')) {
    return 'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?auto=format&fit=crop&w=640&q=80'
  }
  if (product.item_id.includes('paper')) {
    return 'https://images.unsplash.com/photo-1586075010923-2dd4570fb338?auto=format&fit=crop&w=640&q=80'
  }
  return 'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=640&q=80'
}

async function loadResults(query: string) {
  const normalized = query.trim()
  if (!normalized) {
    products.value = []
    searchedQuery.value = ''
    return
  }

  isLoading.value = true
  errorMessage.value = ''
  searchedQuery.value = normalized

  try {
    const response = await searchProducts(normalized)
    products.value = response.items
  } catch (error) {
    products.value = []
    errorMessage.value =
      error instanceof Error
        ? error.message
        : 'Search service is unavailable. Check the API address and try again.'
  } finally {
    isLoading.value = false
  }
}

function submitSearch() {
  const normalized = searchQuery.value.trim()
  if (!normalized) return
  router.push({ name: 'search', query: { q: normalized } })
}

watch(
  () => route.query.q,
  (query) => {
    searchQuery.value = String(query ?? '')
    loadResults(searchQuery.value)
  },
)

onMounted(() => loadResults(searchQuery.value))
</script>

<template>
  <main class="min-h-screen bg-white text-[#101828]">
    <header class="sticky top-0 z-30 bg-[#0053E2] text-white shadow-sm">
      <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-6 py-4">
        <RouterLink class="flex shrink-0 items-center gap-3" to="/" aria-label="TalonMart home">
          <span class="grid h-11 w-11 place-items-center rounded-full bg-[#FFC220] font-black text-[#0053E2]">
            TM
          </span>
          <span class="text-2xl font-black">TalonMart</span>
        </RouterLink>

        <button
          class="hidden min-h-12 items-center gap-3 rounded-full bg-[#003A9B] px-4 text-left text-sm font-semibold xl:flex"
          type="button"
        >
          <MapPin class="h-5 w-5 text-[#FFC220]" aria-hidden="true" />
          <span>
            <span class="block text-xs text-white/75">Pickup or delivery?</span>
            <span>Sacramento, 95829</span>
          </span>
          <ChevronDown class="h-4 w-4" aria-hidden="true" />
        </button>

        <form class="flex min-h-12 flex-1 overflow-hidden rounded-full bg-white" role="search" @submit.prevent="submitSearch">
          <input
            v-model="searchQuery"
            aria-label="Search products"
            class="min-w-0 flex-1 px-6 text-lg text-[#101828] outline-none"
            placeholder="Search everything at TalonMart"
            type="search"
          />
          <button
            class="grid w-14 place-items-center bg-[#FFC220] text-[#101828] transition hover:bg-[#FFD35A]"
            type="submit"
            aria-label="Submit search"
          >
            <Search class="h-5 w-5" aria-hidden="true" />
          </button>
        </form>

        <RouterLink class="hidden min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex" to="/">
          <UserRound class="h-5 w-5" aria-hidden="true" />
          Account
        </RouterLink>
        <RouterLink class="hidden min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex" to="/">
          <ShoppingCart class="h-5 w-5" aria-hidden="true" />
          Cart
        </RouterLink>
      </div>

      <nav class="border-t border-white/15 bg-[#F3F8FF] text-[#101828]">
        <div class="mx-auto flex max-w-[1440px] gap-3 overflow-x-auto px-6 py-3">
          <button
            v-for="tab in topTabs"
            :key="tab"
            class="flex min-h-10 shrink-0 items-center gap-2 rounded-full bg-white px-5 text-sm font-bold shadow-sm transition hover:bg-[#EAF2FF]"
            type="button"
          >
            {{ tab }}
            <ChevronDown v-if="tab === 'Departments' || tab === 'Services'" class="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </nav>
    </header>

    <section class="border-b border-[#D8E0E8] bg-white">
      <div class="mx-auto flex max-w-[1440px] gap-3 overflow-x-auto px-6 py-4">
        <button
          v-for="filter in quickFilters"
          :key="filter"
          class="flex min-h-11 shrink-0 items-center gap-2 rounded-full border border-[#AEB8C2] px-5 text-base font-semibold transition hover:border-[#0053E2]"
          type="button"
        >
          <Home v-if="filter === 'In-store'" class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          <Truck v-else-if="filter === 'Get it fast'" class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          <BadgeDollarSign v-else-if="filter === 'All deals' || filter === 'Price'" class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          <RefreshCw v-else-if="filter === 'Subscription'" class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          <SlidersHorizontal v-else class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          {{ filter }}
          <ChevronDown v-if="filter === 'Price' || filter === 'Brand'" class="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </section>

    <section class="border-b border-[#D8E0E8] bg-white">
      <div class="mx-auto flex max-w-[1440px] gap-8 overflow-x-auto px-6 py-6">
        <button
          v-for="shortcut in milkShortcuts"
          :key="shortcut.label"
          class="grid min-w-[120px] justify-items-center gap-3 text-center text-base font-semibold"
          type="button"
        >
          <span class="grid h-24 w-24 place-items-center overflow-hidden rounded-lg bg-[#F1F5F9]">
            <span v-if="!shortcut.image" class="text-sm font-black text-[#0053E2]">Shop all</span>
            <img v-else :alt="shortcut.label" :src="shortcut.image" class="h-full w-full object-cover" />
          </span>
          <span>{{ shortcut.label }}</span>
        </button>
      </div>
    </section>

    <section class="mx-auto grid max-w-[1440px] gap-8 px-6 py-6 xl:grid-cols-[260px_1fr]">
      <aside class="hidden xl:block">
        <div class="sticky top-[150px] space-y-6">
          <section class="border-b border-[#D8E0E8] pb-5">
            <h2 class="text-lg font-black">Filter by</h2>
            <button class="mt-4 flex min-h-10 w-full items-center justify-between text-left font-bold" type="button">
              Departments
              <ChevronDown class="h-4 w-4" aria-hidden="true" />
            </button>
            <div class="mt-3 grid gap-2">
              <button
                v-for="department in departments"
                :key="department"
                class="text-left text-sm text-[#344054] hover:text-[#0053E2]"
                type="button"
              >
                {{ department }}
              </button>
            </div>
          </section>

          <section class="border-b border-[#D8E0E8] pb-5">
            <button class="flex min-h-10 w-full items-center justify-between text-left font-bold" type="button">
              Availability
              <ChevronDown class="h-4 w-4" aria-hidden="true" />
            </button>
            <label class="mt-3 flex items-center gap-3 text-sm">
              <input class="h-4 w-4" type="checkbox" />
              In stock
            </label>
          </section>

          <section>
            <button class="flex min-h-10 w-full items-center justify-between text-left font-bold" type="button">
              Warehouse
              <ChevronDown class="h-4 w-4" aria-hidden="true" />
            </button>
            <div class="mt-3 grid gap-2 text-sm text-[#344054]">
              <span>wh_hk_1</span>
              <span>wh_sz_1</span>
            </div>
          </section>
        </div>
      </aside>

      <section>
        <div class="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p class="text-sm text-[#667085]">Home / Search</p>
            <h1 class="mt-2 text-3xl font-black">Results for "{{ searchedQuery || searchQuery }}"</h1>
            <p class="mt-1 text-sm text-[#667085]">{{ resultCountLabel }} from live inventory balances</p>
          </div>

          <button
            class="flex min-h-11 items-center gap-2 rounded-full border border-[#AEB8C2] px-5 text-sm font-bold hover:border-[#0053E2]"
            type="button"
          >
            Sort by: Best Match
            <ChevronDown class="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div v-if="isLoading" class="grid min-h-[280px] place-items-center rounded-lg border border-[#D8E0E8] bg-[#F8FAFC]">
          <div class="flex items-center gap-3 text-lg font-bold text-[#0053E2]">
            <LoaderCircle class="h-6 w-6 animate-spin" aria-hidden="true" />
            Loading search results
          </div>
        </div>

        <div v-else-if="errorMessage" class="rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-6 text-[#991B1B]" role="alert">
          <h2 class="text-lg font-black">Search request failed</h2>
          <p class="mt-2 text-sm">{{ errorMessage }}</p>
        </div>

        <div v-else-if="products.length === 0" class="rounded-lg border border-[#D8E0E8] bg-[#F8FAFC] p-8">
          <h2 class="text-xl font-black">No results found</h2>
          <p class="mt-2 text-[#667085]">Try a different product keyword such as milk, cola, paper, or tissue.</p>
        </div>

        <div v-else class="grid gap-x-5 gap-y-8 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          <article
            v-for="product in products"
            :key="product.item_id"
            class="group rounded-lg border border-transparent bg-white p-3 transition hover:border-[#D8E0E8] hover:shadow-md"
          >
            <div class="relative aspect-square overflow-hidden rounded-lg bg-[#F1F5F9]">
              <img :alt="product.item_name" :src="productImage(product)" class="h-full w-full object-cover transition group-hover:scale-105" />
              <span class="absolute left-3 top-3 rounded-full bg-white px-3 py-1 text-xs font-black text-[#0053E2] shadow-sm">
                {{ stockLabel(product) }}
              </span>
            </div>

            <div class="mt-4">
              <p class="text-xs font-black uppercase text-[#667085]">{{ product.brand }} / {{ product.category_id }}</p>
              <h2 class="mt-1 min-h-14 text-lg font-bold leading-tight">{{ product.item_name }}</h2>
              <p class="mt-1 text-sm text-[#667085]">{{ product.spec }}</p>

              <p class="mt-3 text-2xl font-black text-[#101828]">{{ totalStock(product) }} units</p>
              <p class="text-sm font-semibold text-[#039855]">Available for local fulfillment</p>

              <button class="mt-4 min-h-11 w-full rounded-full bg-[#0053E2] font-black text-white transition hover:bg-[#003A9B]" type="button">
                Add to cart
              </button>
            </div>
          </article>
        </div>
      </section>
    </section>
  </main>
</template>
