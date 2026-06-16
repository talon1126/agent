<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ChevronLeft, LoaderCircle, Search, Star } from 'lucide-vue-next'

import StoreHeader from '@/components/StoreHeader.vue'
import { searchProductsByCategory } from '@/services/searchApi'
import type { SearchProduct } from '@/types/search'

const route = useRoute()

const departmentLabels: Record<string, string> = {
  grocery: 'Grocery',
  'clothing-shoes-accessories': 'Clothing, Shoes & Accessories',
  'baby-kids': 'Baby & Kids',
  electronics: 'Electronics',
}

const products = ref<SearchProduct[]>([])
const isLoading = ref(false)
const errorMessage = ref('')

const departmentSlug = computed(() => String(route.params.departmentSlug ?? ''))
const departmentLabel = computed(() => departmentLabels[departmentSlug.value] ?? 'Department')
const resultCountLabel = computed(() =>
  products.value.length === 1 ? '1 product' : `${products.value.length} products`,
)

function totalStock(product: SearchProduct) {
  return product.balances.reduce((total, balance) => total + balance.quantity_on_hand, 0)
}

function productImage(product: SearchProduct) {
  if (product.category_id === 'electronics') {
    return 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=640&q=80'
  }
  if (product.category_id === 'clothing_shoes_accessories') {
    return 'https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=640&q=80'
  }
  if (product.category_id === 'baby_kids') {
    return 'https://images.unsplash.com/photo-1519689680058-324335c77eba?auto=format&fit=crop&w=640&q=80'
  }
  return 'https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=640&q=80'
}

function productRating(product: SearchProduct) {
  return product.rating
}

function formatCount(value: number) {
  return new Intl.NumberFormat('en-US').format(value)
}

async function loadDepartmentProducts() {
  // Category pages are powered by the deterministic `category` search parameter, not keyword search.
  isLoading.value = true
  errorMessage.value = ''

  try {
    const response = await searchProductsByCategory(departmentSlug.value)
    products.value = response.items
  } catch (error) {
    products.value = []
    errorMessage.value =
      error instanceof Error
        ? error.message
        : 'Department service is unavailable. Check the API address and try again.'
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  void loadDepartmentProducts()
})

watch(departmentSlug, () => {
  void loadDepartmentProducts()
})
</script>

<template>
  <main class="min-h-screen bg-white text-[#101828]">
    <StoreHeader />

    <section class="mx-auto max-w-[1440px] px-6 py-6">
      <RouterLink
        class="inline-flex min-h-10 items-center gap-2 rounded-full border border-[#AEB8C2] bg-white px-4 text-sm font-bold text-[#344054] hover:border-[#0053E2]"
        to="/"
      >
        <ChevronLeft class="h-4 w-4" aria-hidden="true" />
        Back to home
      </RouterLink>

      <div class="mt-6 rounded-lg bg-white p-6 shadow-sm">
        <p class="text-sm font-black uppercase text-[#0053E2]">Department</p>
        <h1 class="mt-2 text-4xl font-black">{{ departmentLabel }}</h1>
        <p class="mt-2 text-sm text-[#667085]">
          {{ resultCountLabel }} from the live category search API.
        </p>
      </div>

      <div
        v-if="isLoading"
        class="mt-6 grid min-h-[280px] place-items-center rounded-lg border border-[#D8E0E8] bg-white"
      >
        <div class="flex items-center gap-3 text-lg font-bold text-[#0053E2]">
          <LoaderCircle class="h-6 w-6 animate-spin" aria-hidden="true" />
          Loading department products
        </div>
      </div>

      <div
        v-else-if="errorMessage"
        class="mt-6 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-6 text-[#991B1B]"
        role="alert"
      >
        <h2 class="text-lg font-black">Department request failed</h2>
        <p class="mt-2 text-sm">{{ errorMessage }}</p>
      </div>

      <div
        v-else-if="products.length === 0"
        class="mt-6 rounded-lg border border-[#D8E0E8] bg-white p-8"
      >
        <h2 class="text-xl font-black">No products found in {{ departmentLabel }}</h2>
        <p class="mt-2 text-[#667085]">
          Try another department or use search to browse all available products.
        </p>
      </div>

      <div v-else class="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <article
          v-for="product in products"
          :key="product.item_id"
          class="rounded-lg bg-white p-4 transition hover:-translate-y-0.5 hover:shadow-md"
          :data-testid="`product-card-${product.item_id}`"
        >
          <RouterLink
            class="block aspect-square overflow-hidden rounded-lg bg-[#EDF4FF]"
            :to="{ name: 'product-detail', params: { item_id: product.item_id } }"
          >
            <img
              :alt="product.item_name"
              :src="productImage(product)"
              class="h-full w-full object-cover"
            />
          </RouterLink>
          <p class="mt-4 text-xs font-black uppercase text-[#667085]">
            {{ product.brand }} / {{ product.category_id }}
          </p>
          <RouterLink
            class="mt-1 block min-h-14 text-lg font-black leading-tight hover:text-[#0053E2] hover:underline"
            :to="{ name: 'product-detail', params: { item_id: product.item_id } }"
          >
            {{ product.item_name }}
          </RouterLink>
          <div
            class="mt-2 flex items-center gap-1 text-sm font-semibold text-[#344054]"
            :data-testid="`product-rating-${product.item_id}`"
          >
            <template v-if="productRating(product)">
              <span class="flex items-center gap-0.5 text-[#FFC220]" aria-hidden="true">
                <Star
                  v-for="index in 5"
                  :key="index"
                  class="h-4 w-4"
                  :fill="
                    index <= Math.round(productRating(product)!.score) ? 'currentColor' : 'none'
                  "
                  :stroke-width="2.4"
                />
              </span>
              <span>{{ productRating(product)!.score.toFixed(1) }}</span>
              <span class="text-[#667085]">
                {{ formatCount(productRating(product)!.count) }} ratings
              </span>
            </template>
            <span v-else class="text-[#667085]">No ratings yet</span>
          </div>
          <p class="mt-1 text-sm text-[#667085]">{{ product.spec }}</p>
          <p class="mt-3 text-2xl font-black text-[#101828]">${{ product.price.toFixed(2) }}</p>
          <p class="mt-1 flex items-center gap-2 text-sm font-bold text-[#039855]">
            <Search class="h-4 w-4" aria-hidden="true" />
            {{ totalStock(product) }} units available
          </p>
        </article>
      </div>
    </section>
  </main>
</template>
