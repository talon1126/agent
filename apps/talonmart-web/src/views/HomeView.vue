<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  BadgePercent,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
} from 'lucide-vue-next'
import StoreHeader from '@/components/StoreHeader.vue'
import { CART_USER_ID } from '@/services/cartApi'
import { fetchHomeHotRankings } from '@/services/categoryRankingApi'
import { fetchFlashSales, purchaseFlashSaleWithDefaultAddress } from '@/services/flashSaleApi'
import type { CategoryRankingItem } from '@/types/categoryRanking'
import type { FlashSale } from '@/types/flashSale'

const router = useRouter()

type HeroSlide = {
  accent: string
  cta: string
  description: string
  image: string
  imageAlt: string
  searchTerm: string
  title: string
}

const heroSlides: HeroSlide[] = [
  {
    accent: 'Fast pickup essentials',
    cta: 'Search grocery deals',
    description:
      'Build a basket from live warehouse stock and keep daily essentials ready for pickup.',
    image:
      'https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=900&q=80',
    imageAlt: 'Fresh groceries arranged in a shopping basket',
    searchTerm: 'milk',
    title: 'Weekend cart refresh',
  },
  {
    accent: 'New tech arrivals',
    cta: 'Shop electronics',
    description:
      'Compare in-stock devices, accessories, and smart home picks before checkout.',
    image:
      'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=900&q=80',
    imageAlt: 'Laptop and electronics on a bright retail desk',
    searchTerm: 'electronics',
    title: 'Electronics that ship fast',
  },
  {
    accent: 'Family-ready finds',
    cta: 'Browse kids picks',
    description:
      'Find practical clothing, baby care, and school-day basics from the same catalog.',
    image:
      'https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?auto=format&fit=crop&w=900&q=80',
    imageAlt: 'Colorful folded children clothes and small toys',
    searchTerm: 'baby kids',
    title: 'Everything for busy family days',
  },
]

type FlashSaleMeta = {
  category: string
  image: string
  title: string
}

// Flash-sale responses carry campaign data and item IDs; the storefront maps IDs to card media.
const flashSaleMetaByItemId: Record<string, FlashSaleMeta> = {
  item_milk_pure: {
    category: 'Dairy',
    image:
      'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=640&q=80',
    title: 'Pure milk flash deal',
  },
  item_cola_zero: {
    category: 'Beverages',
    image:
      'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?auto=format&fit=crop&w=640&q=80',
    title: 'Zero sugar cola flash pack',
  },
  item_vinda_tissue: {
    category: 'Paper Goods',
    image:
      'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=640&q=80',
    title: 'Soft tissue family flash deal',
  },
  item_yogurt_plain: {
    category: 'Dairy',
    image:
      'https://images.unsplash.com/photo-1488477181946-6428a0291777?auto=format&fit=crop&w=640&q=80',
    title: 'Plain yogurt flash bundle',
  },
  item_water_spring: {
    category: 'Beverages',
    image:
      'https://images.unsplash.com/photo-1616118132534-381148898bb4?auto=format&fit=crop&w=640&q=80',
    title: 'Spring water case flash deal',
  },
  item_detergent: {
    category: 'Household',
    image:
      'https://images.unsplash.com/photo-1624372524708-543ca5344a83?auto=format&fit=crop&w=640&q=80',
    title: 'Laundry detergent flash deal',
  },
  item_office_pen: {
    category: 'Office Supplies',
    image:
      'https://images.unsplash.com/photo-1583485088034-697b5bc54ccd?auto=format&fit=crop&w=640&q=80',
    title: 'Office pen flash pack',
  },
  item_copy_paper: {
    category: 'Office Supplies',
    image:
      'https://images.unsplash.com/photo-1586075010923-2dd4570fb338?auto=format&fit=crop&w=640&q=80',
    title: 'A4 copy paper flash deal',
  },
}

const searchQuery = ref('milk')
const searchError = ref('')
const flashSales = ref<FlashSale[]>([])
const flashSaleError = ref('')
const flashSaleSuccess = ref('')
const hotRankings = ref<CategoryRankingItem[]>([])
const hotRankingError = ref('')
const isHotRankingLoading = ref(false)
const isFlashSaleLoading = ref(false)
const pendingFlashSaleId = ref<number | null>(null)
const activeSlideIndex = ref(0)

const flashSaleListReady = computed(() => flashSales.value.length > 0)
const heroTrackTransform = computed(
  () => `translate3d(${activeSlideIndex.value === 0 ? 0 : -activeSlideIndex.value * 100}%, 0, 0)`,
)

function handleSearch() {
  const query = searchQuery.value.trim()

  if (!query) {
    searchError.value = 'Enter a product keyword to search inventory.'
    return
  }

  searchError.value = ''
  router.push({ name: 'search', query: { q: query } })
}

function runHeroSearch(slide: HeroSlide) {
  searchQuery.value = slide.searchTerm
  handleSearch()
}

function showPreviousSlide() {
  activeSlideIndex.value =
    activeSlideIndex.value === 0 ? heroSlides.length - 1 : activeSlideIndex.value - 1
}

function showNextSlide() {
  activeSlideIndex.value = (activeSlideIndex.value + 1) % heroSlides.length
}

function selectSlide(index: number) {
  activeSlideIndex.value = index
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
  return (
    flashSaleMetaByItemId[sale.item_id] ?? {
      category: 'Flash Deal',
      image:
        'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=640&q=80',
      title: `${humanizeItemId(sale.item_id)} flash deal`,
    }
  )
}

function flashSaleOriginalPrice(sale: FlashSale) {
  if (sale.item_price !== null && sale.item_price !== undefined && sale.item_price > sale.sale_price) {
    return sale.item_price
  }

  return null
}

function flashSaleOriginalPriceLabel(sale: FlashSale) {
  const originalPrice = flashSaleOriginalPrice(sale)

  return originalPrice === null ? null : formatCurrency(originalPrice)
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

function openFlashSaleProduct(sale: FlashSale) {
  router.push({
    name: 'product-detail',
    params: { item_id: sale.item_id },
  })
}

function openHotRankingProduct(item: CategoryRankingItem) {
  router.push({
    name: 'product-detail',
    params: { item_id: item.item_id },
  })
}

function rankingProductImage(item: CategoryRankingItem) {
  if (item.category_id === 'electronics') {
    return 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=640&q=80'
  }
  if (item.category_id === 'dairy') {
    return 'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=640&q=80'
  }
  if (item.category_id === 'paper') {
    return 'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=640&q=80'
  }
  return 'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=640&q=80'
}

async function loadFlashSales() {
  // Stock is weakly real-time: the page refreshes from the backend instead of polling locally.
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

async function loadHomeHotRankings() {
  // The homepage rail reads backend ranking snapshots so product popularity is not hardcoded in Vue.
  isHotRankingLoading.value = true
  hotRankingError.value = ''

  try {
    const response = await fetchHomeHotRankings({ limit: 8 })
    hotRankings.value = response.items
  } catch (error) {
    hotRankings.value = []
    hotRankingError.value =
      error instanceof Error ? error.message : 'Unable to load popular products right now.'
  } finally {
    isHotRankingLoading.value = false
  }
}

async function buyFlashSale(sale: FlashSale) {
  if (!canBuyFlashSale(sale)) {
    return
  }

  // Flash-sale checkout uses the default address and trusts the backend atomic stock claim result.
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
  void loadHomeHotRankings()
})
</script>

<template>
  <main class="min-h-screen bg-[#F5F7FA] text-[#101828]">
    <StoreHeader :initial-search-query="searchQuery" />

    <section class="mx-auto max-w-[1440px] px-6 py-6">
      <div class="grid gap-5">
        <section
          class="relative min-h-[330px] overflow-hidden rounded-[8px] bg-[#0053E2] text-white shadow-[0_18px_46px_rgba(0,83,226,0.22)]"
          data-testid="home-hero-carousel"
        >
          <div
            class="flex min-h-[330px] transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]"
            data-testid="home-hero-track"
            :style="{ transform: heroTrackTransform }"
          >
            <article
              v-for="slide in heroSlides"
              :key="slide.title"
              class="relative grid min-w-full items-center gap-6 overflow-hidden lg:grid-cols-[1fr_0.86fr]"
            >
              <div
                class="absolute inset-y-0 right-0 hidden w-[46%] bg-[#FFC220] lg:block"
                aria-hidden="true"
              ></div>
              <div class="relative px-7 py-8 sm:px-10 lg:px-12">
                <p class="text-sm font-black uppercase tracking-normal text-[#FFC220]">
                  {{ slide.accent }}
                </p>
                <h1 class="mt-3 max-w-[640px] text-4xl font-black leading-[1.02] sm:text-5xl lg:text-[58px]">
                  {{ slide.title }}
                </h1>
                <p class="mt-5 max-w-xl text-base font-semibold leading-7 text-white/86">
                  {{ slide.description }}
                </p>
                <button
                  class="mt-7 min-h-11 rounded-full bg-white px-6 text-sm font-black text-[#0053E2] shadow-sm transition hover:bg-[#EAF2FF]"
                  type="button"
                  @click="runHeroSearch(slide)"
                >
                  {{ slide.cta }}
                </button>
              </div>

              <div class="relative min-h-[260px] overflow-hidden lg:min-h-[330px]">
                <img
                  class="absolute inset-0 h-full w-full object-cover transition-transform duration-700 group-hover:scale-[1.02]"
                  :alt="slide.imageAlt"
                  :src="slide.image"
                  data-testid="home-hero-slide"
                />
                <div class="absolute inset-0 bg-gradient-to-r from-[#0053E2] via-transparent to-transparent lg:from-transparent"></div>
              </div>
            </article>
          </div>

          <button
            class="absolute left-4 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/95 text-[#0053E2] shadow-md transition hover:bg-[#EAF2FF]"
            type="button"
            aria-label="Previous promotional slide"
            data-testid="home-hero-prev"
            @click="showPreviousSlide"
          >
            <ChevronLeft class="h-5 w-5" aria-hidden="true" />
          </button>
          <button
            class="absolute right-4 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/95 text-[#0053E2] shadow-md transition hover:bg-[#EAF2FF]"
            type="button"
            aria-label="Next promotional slide"
            data-testid="home-hero-next"
            @click="showNextSlide"
          >
            <ChevronRight class="h-5 w-5" aria-hidden="true" />
          </button>
          <div class="absolute bottom-5 left-1/2 flex -translate-x-1/2 gap-2" aria-label="Carousel slides">
            <button
              v-for="(_slide, index) in heroSlides"
              :key="index"
              class="h-2.5 rounded-full transition-all"
              :class="index === activeSlideIndex ? 'w-8 bg-[#FFC220]' : 'w-2.5 bg-white/70'"
              type="button"
              :aria-label="`Show promotional slide ${index + 1}`"
              @click="selectSlide(index)"
            ></button>
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
              class="group rounded-lg bg-white p-0 transition hover:-translate-y-0.5"
              data-testid="flash-sale-card"
            >
              <button
                class="relative block aspect-[4/3] w-full overflow-hidden rounded-md bg-white text-left"
                :data-testid="`flash-sale-detail-${sale.item_id}`"
                type="button"
                :aria-label="`View details for ${flashSaleMeta(sale).title}`"
                @click="openFlashSaleProduct(sale)"
              >
                <img
                  :alt="flashSaleMeta(sale).title"
                  :src="flashSaleMeta(sale).image"
                  class="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
                />
                <span
                  class="absolute left-3 top-3 rounded-md bg-white px-2 py-1 text-xs font-black text-[#1A7F00]"
                >
                  Reduced price
                </span>
              </button>
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
                <span
                  v-if="flashSaleOriginalPriceLabel(sale) !== null"
                  class="ml-1 text-xs font-medium text-[#667085] line-through"
                >
                  {{ flashSaleOriginalPriceLabel(sale) }}
                </span>
              </p>
              <button
                class="mt-1 min-h-12 text-left text-sm font-semibold leading-snug text-[#344054] hover:underline"
                type="button"
                :aria-label="`View details for ${flashSaleMeta(sale).title}`"
                @click="openFlashSaleProduct(sale)"
              >
                {{ flashSaleMeta(sale).title }}
              </button>
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

        <section class="bg-white px-0 py-8">
          <div class="mb-5 flex items-end justify-between gap-4">
            <div>
              <p class="text-sm font-black uppercase text-[#0053E2]">Popular now</p>
              <h2 class="mt-1 text-2xl font-black">Bet you like it.</h2>
            </div>
          </div>

          <p
            v-if="hotRankingError"
            class="mb-4 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-sm font-bold text-[#991B1B]"
            role="alert"
          >
            {{ hotRankingError }}
          </p>

          <div
            v-if="isHotRankingLoading"
            class="grid min-h-[180px] place-items-center rounded-lg border border-[#D8E0E8] bg-[#FCFCFD]"
          >
            <div class="flex items-center gap-3 text-sm font-bold text-[#344054]">
              <LoaderCircle class="h-5 w-5 animate-spin text-[#00A6C8]" aria-hidden="true" />
              Loading popular products
            </div>
          </div>

          <div
            v-else-if="hotRankings.length === 0"
            class="rounded-lg border border-[#D8E0E8] bg-[#F7F8FA] p-6"
          >
            <p class="text-sm font-bold text-[#667085]">Ranking data is warming up</p>
          </div>

          <div v-else class="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <article
              v-for="item in hotRankings"
              :key="item.item_id"
              class="rounded-lg bg-white transition hover:-translate-y-0.5"
            >
              <button
                class="block w-full text-left"
                type="button"
                :data-testid="`home-hot-product-${item.item_id}`"
                @click="openHotRankingProduct(item)"
              >
                <span class="block aspect-[4/3] overflow-hidden rounded-md bg-white">
                  <img
                    class="h-full w-full object-cover"
                    :alt="item.item_name"
                    :src="rankingProductImage(item)"
                  />
                </span>
                <span class="mt-3 block text-xs font-black uppercase text-[#0053E2]">
                  #{{ item.rank }} in {{ item.category_name || item.category_id }}
                </span>
                <span class="mt-1 block min-h-12 text-base font-black leading-tight">
                  {{ item.item_name }}
                </span>
                <span class="mt-2 block text-sm text-[#667085]">{{ item.brand }} / {{ item.spec }}</span>
                <span class="mt-3 block text-xl font-black">{{ formatCurrency(item.price) }}</span>
              </button>
            </article>
          </div>
        </section>

      </div>
    </section>
  </main>
</template>
