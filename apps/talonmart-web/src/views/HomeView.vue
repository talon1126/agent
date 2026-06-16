<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  BadgePercent,
  LoaderCircle,
  PackageCheck,
} from 'lucide-vue-next'
import StoreHeader from '@/components/StoreHeader.vue'
import { CART_USER_ID } from '@/services/cartApi'
import { fetchFlashSales, purchaseFlashSaleWithDefaultAddress } from '@/services/flashSaleApi'
import type { FlashSale } from '@/types/flashSale'

const departmentMenuItems = [
  { label: 'All Departments' },
  { label: 'Rollbacks & more' },
  { label: "Father's Day" },
  { label: 'Grocery', slug: 'grocery' },
  { label: 'Clothing, Shoes & Accessories', slug: 'clothing-shoes-accessories' },
  { label: 'Baby & Kids', slug: 'baby-kids' },
  { label: 'Pharmacy' },
  { label: 'Health & Wellness' },
  { label: 'Home' },
  { label: 'Garden & Tools' },
  { label: 'Electronics', slug: 'electronics' },
  { label: 'Gaming & Movies' },
  { label: 'Auto & Tires' },
  { label: 'Personal Care' },
  { label: 'Beauty' },
  { label: 'Toys & Outdoor Play' },
  { label: 'Household Essentials' },
]
const router = useRouter()

const deals = [
  {
    id: 'item_vinda_tissue',
    category: 'Paper Goods',
    name: 'Vinda soft tissue 3-ply family pack',
    image:
      'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=640&q=80',
    price: '$12.80',
    originalPrice: '$15.90',
    stock: 'In stock',
  },
  {
    id: 'item_milk_pure',
    category: 'Dairy',
    name: 'Pure milk 1L multipack',
    image:
      'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=640&q=80',
    price: '$18.40',
    originalPrice: '$21.00',
    stock: 'Low stock',
  },
  {
    id: 'item_cola_zero',
    category: 'Beverages',
    name: 'Zero sugar cola cans 12 pack',
    image:
      'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?auto=format&fit=crop&w=640&q=80',
    price: '$9.90',
    originalPrice: '$12.50',
    stock: 'In stock',
  },
  {
    id: 'item_copy_paper',
    category: 'Office Supplies',
    name: 'A4 copy paper 500 sheets',
    image:
      'https://images.unsplash.com/photo-1586075010923-2dd4570fb338?auto=format&fit=crop&w=640&q=80',
    price: '$6.20',
    originalPrice: '$7.40',
    stock: 'In stock',
  },
]

type FlashSaleMeta = {
  category: string
  image: string
  originalPrice: number
  title: string
}

// 中文注释：后端秒杀列表当前只返回活动和商品 ID，前端先用商品 ID 补足首页卡片展示信息。
const flashSaleMetaByItemId: Record<string, FlashSaleMeta> = {
  item_milk_pure: {
    category: 'Dairy',
    image:
      'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=640&q=80',
    originalPrice: 18.4,
    title: 'Pure milk flash deal',
  },
  item_cola_zero: {
    category: 'Beverages',
    image:
      'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?auto=format&fit=crop&w=640&q=80',
    originalPrice: 24.9,
    title: 'Zero sugar cola flash pack',
  },
  item_vinda_tissue: {
    category: 'Paper Goods',
    image:
      'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=640&q=80',
    originalPrice: 24.8,
    title: 'Soft tissue family flash deal',
  },
  item_yogurt_plain: {
    category: 'Dairy',
    image:
      'https://images.unsplash.com/photo-1488477181946-6428a0291777?auto=format&fit=crop&w=640&q=80',
    originalPrice: 21.5,
    title: 'Plain yogurt flash bundle',
  },
  item_water_spring: {
    category: 'Beverages',
    image:
      'https://images.unsplash.com/photo-1616118132534-381148898bb4?auto=format&fit=crop&w=640&q=80',
    originalPrice: 25.9,
    title: 'Spring water case flash deal',
  },
  item_detergent: {
    category: 'Household',
    image:
      'https://images.unsplash.com/photo-1624372524708-543ca5344a83?auto=format&fit=crop&w=640&q=80',
    originalPrice: 52.9,
    title: 'Laundry detergent flash deal',
  },
  item_office_pen: {
    category: 'Office Supplies',
    image:
      'https://images.unsplash.com/photo-1583485088034-697b5bc54ccd?auto=format&fit=crop&w=640&q=80',
    originalPrice: 9.9,
    title: 'Office pen flash pack',
  },
  item_copy_paper: {
    category: 'Office Supplies',
    image:
      'https://images.unsplash.com/photo-1586075010923-2dd4570fb338?auto=format&fit=crop&w=640&q=80',
    originalPrice: 25.9,
    title: 'A4 copy paper flash deal',
  },
}

const searchQuery = ref('milk')
const searchError = ref('')
const flashSales = ref<FlashSale[]>([])
const flashSaleError = ref('')
const flashSaleSuccess = ref('')
const isFlashSaleLoading = ref(false)
const pendingFlashSaleId = ref<number | null>(null)

const flashSaleListReady = computed(() => flashSales.value.length > 0)

function handleSearch() {
  const query = searchQuery.value.trim()

  if (!query) {
    searchError.value = 'Enter a product keyword to search inventory.'
    return
  }

  searchError.value = ''
  router.push({ name: 'search', query: { q: query } })
}

function openDepartment(slug?: string) {
  // Department links are intentionally limited to categories backed by mock-api catalog data.
  if (!slug) {
    return
  }

  router.push(`/cp/${slug}`)
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    currency: 'USD',
    style: 'currency',
  }).format(value)
}

function humanizeItemId(itemId: string) {
  const words = itemId
    .replace(/^item_/, '')
    .split('_')
    .filter(Boolean)
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
}

function flashSaleMeta(sale: FlashSale): FlashSaleMeta {
  const fallbackPrice = Math.round(sale.sale_price * 1.35 * 100) / 100

  return (
    flashSaleMetaByItemId[sale.item_id] ?? {
      category: 'Flash Deal',
      image:
        'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=640&q=80',
      originalPrice: fallbackPrice,
      title: `${humanizeItemId(sale.item_id)} flash deal`,
    }
  )
}

function flashSaleStockLabel(sale: FlashSale) {
  if (sale.stock_remaining === null) {
    return 'Stock pending'
  }

  if (sale.stock_remaining <= 0) {
    return 'Sold out'
  }

  return `${sale.stock_remaining} left`
}

function canBuyFlashSale(sale: FlashSale) {
  return sale.status === 'active' && sale.stock_remaining !== null && sale.stock_remaining > 0
}

async function loadFlashSales() {
  // 中文注释：库存弱实时策略是用户刷新页面时重新查询一次，不在前端做轮询扣减。
  isFlashSaleLoading.value = true
  flashSaleError.value = ''

  try {
    const response = await fetchFlashSales({ status: 'active', limit: 20 })
    flashSales.value = response.flash_sales
  } catch (error) {
    flashSales.value = []
    flashSaleError.value =
      error instanceof Error ? error.message : 'Unable to load flash deals right now.'
  } finally {
    isFlashSaleLoading.value = false
  }
}

async function buyFlashSale(sale: FlashSale) {
  if (!canBuyFlashSale(sale)) {
    return
  }

  // 中文注释：秒杀购买复用默认收货地址，抢购和扣库存结果以后端原子接口返回为准。
  pendingFlashSaleId.value = sale.id
  flashSaleError.value = ''
  flashSaleSuccess.value = ''

  try {
    const response = await purchaseFlashSaleWithDefaultAddress(sale.id, CART_USER_ID)
    flashSaleSuccess.value = `Order created: ${response.order.order_id}`
    await loadFlashSales()
  } catch (error) {
    flashSaleError.value =
      error instanceof Error ? error.message : 'Unable to purchase this flash deal.'
  } finally {
    pendingFlashSaleId.value = null
  }
}

onMounted(() => {
  void loadFlashSales()
})
</script>

<template>
  <main class="min-h-screen bg-[#F5F7FA] text-[#101828]">
    <StoreHeader :initial-search-query="searchQuery" />

    <section class="mx-auto grid max-w-[1440px] gap-5 px-6 py-6 xl:grid-cols-[280px_1fr]">
      <aside class="rounded-lg border border-[#D8E0E8] bg-white p-4">
        <h2 class="mb-3 text-base font-bold">Shop by department</h2>
        <div class="grid gap-2">
          <button
            v-for="item in departmentMenuItems.filter((department) => department.slug)"
            :key="item.label"
            class="rounded-md border border-transparent px-3 py-3 text-sm font-semibold hover:border-[#00A6C8] hover:bg-[#E6F8FB]"
            type="button"
            :data-testid="item.slug ? `department-card-${item.slug}` : undefined"
            @click="openDepartment(item.slug)"
          >
            {{ item.label }}
          </button>
        </div>
      </aside>

      <div class="grid gap-5">
        <section
          class="grid min-h-[260px] overflow-hidden rounded-lg bg-[#0F2A44] text-white lg:grid-cols-[1.2fr_0.8fr]"
        >
          <div class="p-8">
            <p class="mb-3 text-sm font-bold uppercase tracking-normal text-[#8BE8F7]">
              Today deals
            </p>
            <h1 class="max-w-2xl text-3xl font-bold leading-tight lg:text-4xl">
              Stock-ready daily essentials with fast local fulfillment.
            </h1>
            <p class="mt-4 max-w-xl text-base text-white/75">
              Search product inventory from the live warehouse API, then compare stock by warehouse
              before the storefront is connected to checkout.
            </p>
            <button
              class="mt-6 min-h-11 rounded-md bg-[#FFB020] px-5 font-bold text-[#0F2A44] hover:bg-[#FFC44D]"
              type="button"
              @click="handleSearch"
            >
              Search milk inventory
            </button>
          </div>
          <div class="grid bg-[#E6F8FB] p-6 text-[#0F2A44]">
            <div class="rounded-lg border border-[#B9EDF5] bg-white p-5">
              <PackageCheck class="mb-4 h-10 w-10 text-[#00A6C8]" aria-hidden="true" />
              <p class="text-sm font-bold uppercase text-[#667085]">Fulfillment signal</p>
              <p class="mt-2 text-3xl font-bold">Ready to ship</p>
              <p class="mt-3 text-sm text-[#667085]">
                Consumer-facing stock messages stay simple while the backend handles warehouse
                selection.
              </p>
            </div>
          </div>
        </section>

        <p
          v-if="searchError"
          class="rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-sm font-semibold text-[#991B1B]"
          role="alert"
        >
          {{ searchError }}
        </p>

        <section class="border-y border-[#E4E7EC] bg-white px-0 py-8">
          <div class="mb-5 flex items-end justify-between gap-4">
            <div>
              <div class="flex items-center gap-2">
                <BadgePercent class="h-7 w-7 text-[#1A7F00]" aria-hidden="true" />
                <h2 class="text-2xl font-black">Flash Deals</h2>
              </div>
              <p class="mt-1 text-sm text-[#667085]">
                Up to 65% off, refreshed when the page loads.
              </p>
            </div>
            <button
              class="text-sm font-bold underline decoration-[#101828] underline-offset-2 disabled:cursor-not-allowed disabled:text-[#98A2B3] disabled:no-underline"
              type="button"
              :disabled="!flashSaleListReady"
            >
              View all
            </button>
          </div>

          <p
            v-if="flashSaleSuccess"
            class="mb-4 rounded-lg border border-[#B7E4C7] bg-[#F0FFF4] p-4 text-sm font-bold text-[#157347]"
            role="status"
          >
            {{ flashSaleSuccess }}
          </p>
          <p
            v-if="flashSaleError"
            class="mb-4 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-sm font-bold text-[#991B1B]"
            role="alert"
          >
            {{ flashSaleError }}
          </p>

          <div
            v-if="isFlashSaleLoading"
            class="grid min-h-[280px] place-items-center rounded-lg border border-[#D8E0E8] bg-[#FCFCFD]"
          >
            <div class="flex items-center gap-3 text-sm font-bold text-[#344054]">
              <LoaderCircle class="h-5 w-5 animate-spin text-[#00A6C8]" aria-hidden="true" />
              Loading flash deals
            </div>
          </div>

          <div
            v-else-if="flashSales.length === 0"
            class="rounded-lg border border-[#D8E0E8] bg-[#F7F8FA] p-6"
          >
            <p class="text-xs font-black uppercase text-[#667085]">No active campaigns</p>
            <h3 class="mt-2 text-xl font-black">No active flash deals right now</h3>
            <p class="mt-3 max-w-2xl text-sm leading-6 text-[#667085]">
              The storefront queries the backend flash sale list when the page loads. New active
              campaigns will appear here after the backend returns them.
            </p>
          </div>

          <div v-else class="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <article
              v-for="sale in flashSales"
              :key="sale.id"
              class="group rounded-lg border border-[#D8E0E8] bg-white p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
            >
              <div class="relative aspect-[4/3] overflow-hidden rounded-md bg-[#F2F4F7]">
                <img
                  :alt="flashSaleMeta(sale).title"
                  :src="flashSaleMeta(sale).image"
                  class="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
                />
                <span
                  class="absolute left-3 top-3 rounded-md border border-[#B7E4C7] bg-white px-2 py-1 text-xs font-black text-[#1A7F00]"
                >
                  Reduced price
                </span>
              </div>
              <button
                class="mt-3 min-h-10 rounded-full border border-[#667085] px-5 font-black text-[#344054] transition enabled:hover:border-[#0F2A44] enabled:hover:bg-[#F2F4F7] disabled:cursor-not-allowed disabled:border-[#D0D5DD] disabled:text-[#98A2B3]"
                type="button"
                :aria-label="`Buy ${flashSaleMeta(sale).title}`"
                :disabled="!canBuyFlashSale(sale) || pendingFlashSaleId === sale.id"
                @click="buyFlashSale(sale)"
              >
                {{ pendingFlashSaleId === sale.id ? 'Buying' : 'Buy now' }}
              </button>
              <p class="mt-3 text-sm font-bold text-[#1A7F00]">
                Now {{ formatCurrency(sale.sale_price) }}
                <span class="ml-1 text-xs font-medium text-[#667085] line-through">
                  {{ formatCurrency(flashSaleMeta(sale).originalPrice) }}
                </span>
              </p>
              <h3 class="mt-1 min-h-12 text-sm font-semibold leading-snug text-[#344054]">
                {{ flashSaleMeta(sale).title }}
              </h3>
              <p class="mt-2 text-xs font-bold uppercase text-[#667085]">
                {{ flashSaleMeta(sale).category }}
              </p>
              <p
                class="mt-1 text-sm font-bold"
                :class="canBuyFlashSale(sale) ? 'text-[#039855]' : 'text-[#98A2B3]'"
              >
                {{ flashSaleStockLabel(sale) }}
              </p>
            </article>
          </div>
        </section>

        <section>
          <div class="mb-3 flex items-end justify-between">
            <div>
              <h2 class="text-2xl font-bold">Today deals</h2>
              <p class="text-sm text-[#667085]">
                Dense retail cards for the first TalonMart storefront pass.
              </p>
            </div>
            <a class="text-sm font-bold text-[#0F2A44] hover:text-[#00A6C8]" href="#">View all</a>
          </div>

          <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <article
              v-for="deal in deals"
              :key="deal.id"
              class="rounded-lg border border-[#D8E0E8] bg-white p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
            >
              <div class="aspect-[4/3] overflow-hidden rounded-md bg-[#E6F8FB]">
                <img :alt="deal.name" :src="deal.image" class="h-full w-full object-cover" />
              </div>
              <p class="mt-3 text-xs font-bold uppercase text-[#667085]">{{ deal.category }}</p>
              <h3 class="mt-1 min-h-12 text-base font-semibold leading-snug">{{ deal.name }}</h3>
              <div class="mt-2 flex items-baseline gap-2">
                <span class="text-xl font-bold text-[#0F2A44]">{{ deal.price }}</span>
                <span class="text-sm text-[#667085] line-through">{{ deal.originalPrice }}</span>
              </div>
              <p class="mt-2 text-sm font-semibold text-[#039855]">{{ deal.stock }}</p>
              <button
                class="mt-3 min-h-10 w-full rounded-md bg-[#0F2A44] font-bold text-white transition hover:bg-[#123A5D]"
              >
                Add to cart
              </button>
            </article>
          </div>
        </section>
      </div>
    </section>
  </main>
</template>
