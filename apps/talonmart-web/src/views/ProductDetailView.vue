<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  ChevronDown,
  Heart,
  Info,
  LoaderCircle,
  MapPin,
  PackageCheck,
  Search,
  Share2,
  ShoppingCart,
  Star,
  Truck,
  UserRound,
} from 'lucide-vue-next'

import { addCartItem, CART_USER_ID } from '@/services/cartApi'
import { fetchProductDetail } from '@/services/productDetailApi'
import type { ProductDetail, ProductImage } from '@/types/productDetail'

const route = useRoute()
const router = useRouter()

const product = ref<ProductDetail | null>(null)
const selectedImageIndex = ref(0)
const isLoading = ref(false)
const isAddingToCart = ref(false)
const errorMessage = ref('')
const cartMessage = ref('')
const cartErrorMessage = ref('')
const searchQuery = ref('')
const isZooming = ref(false)
const zoomPosition = ref({ x: 50, y: 50 })

const topTabs = [
  'Departments',
  'Services',
  'Rollbacks & More',
  "Father's Day",
  'Get it Fast',
  'Pharmacy',
  'New Arrivals',
  'TalonMart+',
]

const itemId = computed(() => String(route.params.item_id ?? ''))

const sortedImages = computed<ProductImage[]>(() =>
  [...(product.value?.images ?? [])].sort(
    (left, right) => Number(left.sort_order ?? 0) - Number(right.sort_order ?? 0),
  ),
)

const selectedImage = computed(() => sortedImages.value[selectedImageIndex.value] ?? null)

const zoomPreviewStyle = computed(() => ({
  backgroundImage: selectedImage.value?.url ? `url(${selectedImage.value.url})` : 'none',
  backgroundPosition: `${zoomPosition.value.x}% ${zoomPosition.value.y}%`,
  backgroundSize: '220%',
}))

const featureList = computed(() => product.value?.features ?? [])
const detailList = computed(() => product.value?.details ?? [])
const badges = computed(() => product.value?.badges ?? [])

function formatCurrency(value: number | string | undefined) {
  return new Intl.NumberFormat('en-US', {
    currency: product.value?.currency ?? 'USD',
    style: 'currency',
  }).format(Number(value || 0))
}

function formatCount(value: number) {
  return new Intl.NumberFormat('en-US').format(value)
}

function handleImageEnter() {
  if (selectedImage.value?.url) {
    isZooming.value = true
  }
}

function handleImageMove(event: MouseEvent) {
  if (!selectedImage.value?.url) {
    return
  }

  // 中文注释：图片放大效果只记录鼠标相对坐标，放大区域由背景图定位完成。
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (!rect.width || !rect.height) {
    zoomPosition.value = { x: 50, y: 50 }
    return
  }

  const x = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100))
  const y = Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100))
  zoomPosition.value = { x, y }
}

function handleImageLeave() {
  isZooming.value = false
}

async function loadProductDetail() {
  if (!itemId.value) {
    errorMessage.value = 'Item not found.'
    return
  }

  isLoading.value = true
  errorMessage.value = ''
  cartMessage.value = ''
  selectedImageIndex.value = 0

  try {
    // 中文注释：详情页按前端路由参数读取后端 /ip/{item_id}，缺失扩展字段时由模板隐藏对应模块。
    const response = await fetchProductDetail(itemId.value)
    product.value = response.item
  } catch (error) {
    product.value = null
    errorMessage.value =
      error instanceof Error ? error.message : 'Product detail service is unavailable.'
  } finally {
    isLoading.value = false
  }
}

async function handleAddToCart() {
  if (!product.value) {
    return
  }

  isAddingToCart.value = true
  cartMessage.value = ''
  cartErrorMessage.value = ''

  try {
    // 中文注释：商品详情页加入购物车复用现有购物车接口，价格最终仍以后端写入为准。
    await addCartItem({
      user_id: CART_USER_ID,
      item_id: product.value.item_id,
      item_name: product.value.item_name,
      price: Number(product.value.price || 0),
      quantity: 1,
    })
    cartMessage.value = 'Added to cart'
  } catch (error) {
    cartErrorMessage.value = error instanceof Error ? error.message : 'Unable to add item to cart.'
  } finally {
    isAddingToCart.value = false
  }
}

function submitSearch() {
  const normalized = searchQuery.value.trim()
  if (!normalized) return
  router.push({ name: 'search', query: { q: normalized } })
}

watch(
  () => route.params.item_id,
  () => {
    void loadProductDetail()
  },
)

onMounted(() => {
  void loadProductDetail()
})
</script>

<template>
  <main class="min-h-screen bg-white text-[#101828]">
    <header class="sticky top-0 z-30 bg-[#0053E2] text-white shadow-sm">
      <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-6 py-4">
        <RouterLink
          class="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-[#FFC220] font-black text-[#0053E2]"
          to="/"
          aria-label="TalonMart home"
        >
          TM
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

        <form
          class="flex min-h-12 flex-1 overflow-hidden rounded-full bg-white"
          role="search"
          @submit.prevent="submitSearch"
        >
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

        <RouterLink
          class="hidden min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex"
          to="/"
        >
          <UserRound class="h-5 w-5" aria-hidden="true" />
          Account
        </RouterLink>
        <RouterLink
          class="flex min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10"
          to="/cart"
        >
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
            <ChevronDown
              v-if="tab === 'Departments' || tab === 'Services'"
              class="h-4 w-4"
              aria-hidden="true"
            />
          </button>
        </div>
      </nav>
    </header>

    <section
      v-if="isLoading"
      class="mx-auto grid min-h-[520px] max-w-[1440px] place-items-center px-6 py-8"
    >
      <div class="flex items-center gap-3 text-lg font-bold text-[#0053E2]">
        <LoaderCircle class="h-6 w-6 animate-spin" aria-hidden="true" />
        Loading product detail
      </div>
    </section>

    <section v-else-if="errorMessage" class="mx-auto max-w-[960px] px-6 py-10" role="alert">
      <div class="rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-8 text-[#991B1B]">
        <h1 class="text-2xl font-black">Item not found</h1>
        <p class="mt-2">{{ errorMessage }}</p>
        <RouterLink
          class="mt-5 inline-flex min-h-11 items-center rounded-full bg-[#0053E2] px-6 font-black text-white"
          :to="{ name: 'search', query: { q: 'milk' } }"
        >
          Back to search
        </RouterLink>
      </div>
    </section>

    <section
      v-else-if="product"
      class="mx-auto grid max-w-[1440px] gap-8 px-6 py-8 xl:grid-cols-[760px_1fr_360px]"
    >
      <section class="grid gap-5 md:grid-cols-[96px_1fr]">
        <div class="hidden gap-4 md:grid md:content-start">
          <button
            v-for="(image, index) in sortedImages"
            :key="image.url"
            class="aspect-square overflow-hidden rounded-lg border bg-[#F8FAFC] p-1 transition hover:border-[#0053E2]"
            :class="selectedImageIndex === index ? 'border-[#0053E2]' : 'border-[#D8E0E8]'"
            type="button"
            @click="selectedImageIndex = index"
          >
            <img
              :alt="image.alt || product.item_name"
              :src="image.url"
              class="h-full w-full rounded-md object-cover"
            />
          </button>
        </div>

        <div class="relative">
          <div class="absolute right-4 top-4 z-10 grid gap-3">
            <button
              class="grid h-11 w-11 place-items-center rounded-full bg-white shadow-md transition hover:bg-[#F1F5F9]"
              type="button"
              aria-label="Share product"
            >
              <Share2 class="h-5 w-5" aria-hidden="true" />
            </button>
            <button
              class="grid h-11 w-11 place-items-center rounded-full bg-white shadow-md transition hover:bg-[#F1F5F9]"
              type="button"
              aria-label="Save product"
            >
              <Heart class="h-5 w-5" aria-hidden="true" />
            </button>
          </div>

          <div
            data-testid="product-main-image"
            class="grid min-h-[520px] cursor-zoom-in place-items-center overflow-hidden rounded-lg bg-[#F8FAFC]"
            @mouseenter="handleImageEnter"
            @mousemove="handleImageMove"
            @mouseleave="handleImageLeave"
          >
            <img
              v-if="selectedImage"
              :alt="selectedImage.alt || product.item_name"
              :src="selectedImage.url"
              class="max-h-[720px] w-full object-contain"
            />
            <div v-else class="grid gap-3 text-center text-[#667085]">
              <PackageCheck class="mx-auto h-16 w-16 text-[#AEB8C2]" aria-hidden="true" />
              <p class="text-lg font-black">{{ product.item_name }}</p>
              <p class="text-sm">Product image coming soon</p>
            </div>
          </div>

          <div
            v-if="isZooming && selectedImage"
            data-testid="product-zoom-preview"
            class="absolute left-[calc(100%+24px)] top-4 z-20 hidden h-[460px] w-[620px] rounded-lg border border-[#D8E0E8] bg-white bg-no-repeat shadow-2xl xl:block"
            :style="zoomPreviewStyle"
            aria-hidden="true"
          />
        </div>
      </section>

      <section>
        <div class="flex flex-wrap gap-2">
          <span
            v-for="badge in badges"
            :key="badge"
            class="rounded-md bg-[#0B1F3A] px-3 py-1 text-sm font-black text-white"
          >
            {{ badge }}
          </span>
        </div>

        <p class="mt-4 text-sm font-semibold text-[#0053E2] underline">{{ product.brand }}</p>
        <h1 class="mt-2 text-3xl font-black leading-tight">{{ product.item_name }}</h1>
        <p class="mt-2 text-sm text-[#667085]">{{ product.spec }} / {{ product.category_id }}</p>

        <div v-if="product.rating" class="mt-4 flex items-center gap-2 text-sm">
          <span class="flex text-[#F59E0B]">
            <Star v-for="index in 5" :key="index" class="h-4 w-4 fill-current" aria-hidden="true" />
          </span>
          <span class="font-semibold">({{ product.rating.score }})</span>
          <span class="text-[#667085]">|</span>
          <span class="underline">{{ formatCount(product.rating.count) }} ratings</span>
        </div>

        <div class="mt-6 border-t border-[#D8E0E8]">
          <section v-if="product.ingredients" class="border-b border-[#D8E0E8] py-5">
            <div class="flex items-center justify-between gap-4">
              <h2 class="text-xl font-black">Ingredients</h2>
              <ChevronDown class="h-6 w-6" aria-hidden="true" />
            </div>
            <p class="mt-3 text-sm leading-6 text-[#344054]">{{ product.ingredients }}</p>
          </section>

          <section v-if="featureList.length" class="mt-4 rounded-lg border border-[#D8E0E8] p-5">
            <h2 class="text-xl font-black">Key item features</h2>
            <ul class="mt-4 list-disc space-y-2 pl-6 text-base leading-7">
              <li v-for="feature in featureList" :key="feature">{{ feature }}</li>
            </ul>
          </section>

          <section v-if="product.description" class="mt-4 rounded-lg border border-[#D8E0E8] p-5">
            <h2 class="text-xl font-black">Item description</h2>
            <p class="mt-3 leading-7 text-[#344054]">{{ product.description }}</p>
          </section>

          <section v-if="detailList.length" class="mt-4 rounded-lg border border-[#D8E0E8] p-5">
            <h2 class="text-xl font-black">Specs</h2>
            <dl class="mt-4 grid gap-3">
              <div
                v-for="detail in detailList"
                :key="detail.label"
                class="grid gap-2 border-b border-[#EEF2F6] pb-3 sm:grid-cols-[160px_1fr]"
              >
                <dt class="font-bold text-[#667085]">{{ detail.label }}</dt>
                <dd>{{ detail.value }}</dd>
              </div>
            </dl>
          </section>
        </div>
      </section>

      <aside class="space-y-5">
        <section class="rounded-lg bg-[#F7F8FA] p-6 shadow-sm">
          <div class="flex items-baseline gap-3">
            <p class="text-4xl font-black">{{ formatCurrency(product.price) }}</p>
            <p class="text-sm text-[#344054]">{{ product.spec }}</p>
          </div>
          <p class="mt-4 flex items-center gap-2 text-sm">
            Price when purchased online
            <Info class="h-4 w-4" aria-hidden="true" />
          </p>
          <p class="mt-3 flex items-center gap-2 text-sm">
            <span
              class="grid h-5 w-5 place-items-center rounded-full bg-[#0053E2] text-xs text-white"
            >
              R
            </span>
            Free 90-day returns
            <Info class="h-4 w-4" aria-hidden="true" />
          </p>

          <button
            class="mt-5 min-h-12 w-full rounded-full bg-[#0053E2] font-black text-white transition hover:bg-[#003A9B] disabled:cursor-not-allowed disabled:bg-[#8CB7FF]"
            type="button"
            :aria-label="`Add ${product.item_name} to cart`"
            :disabled="isAddingToCart"
            @click="handleAddToCart"
          >
            <span v-if="isAddingToCart" class="inline-flex items-center gap-2">
              <LoaderCircle class="h-5 w-5 animate-spin" aria-hidden="true" />
              Adding
            </span>
            <span v-else>Add to cart</span>
          </button>

          <p
            v-if="cartMessage"
            class="mt-3 rounded-md bg-[#ECFDF3] p-3 text-sm font-bold text-[#027A48]"
            role="status"
          >
            {{ cartMessage }}
          </p>
          <p
            v-if="cartErrorMessage"
            class="mt-3 rounded-md bg-[#FEF2F2] p-3 text-sm font-bold text-[#991B1B]"
            role="alert"
          >
            {{ cartErrorMessage }}
          </p>
        </section>

        <section class="rounded-lg border border-[#D8E0E8] bg-white p-5">
          <h2 class="text-xl font-black">How you'll get this item</h2>
          <div class="mt-4 grid grid-cols-3 gap-3">
            <div
              class="rounded-lg border p-3 text-center"
              :class="
                product.fulfillment?.shipping_available
                  ? 'border-[#0053E2]'
                  : 'border-[#D0D5DD] text-[#667085]'
              "
            >
              <Truck class="mx-auto h-7 w-7" aria-hidden="true" />
              <p class="mt-2 font-bold">Shipping</p>
              <p class="text-xs">
                {{ product.fulfillment?.shipping_available ? 'Available' : 'Not available' }}
              </p>
            </div>
            <div
              class="rounded-lg border p-3 text-center"
              :class="
                product.fulfillment?.pickup_available
                  ? 'border-[#0053E2] bg-[#F5F9FF]'
                  : 'border-[#D0D5DD] text-[#667085]'
              "
            >
              <PackageCheck class="mx-auto h-7 w-7" aria-hidden="true" />
              <p class="mt-2 font-bold">Pickup</p>
              <p class="text-xs">{{ product.fulfillment?.pickup_message ?? 'Check store' }}</p>
            </div>
            <div
              class="rounded-lg border p-3 text-center"
              :class="
                product.fulfillment?.delivery_available
                  ? 'border-[#0053E2]'
                  : 'border-[#D0D5DD] text-[#667085]'
              "
            >
              <ShoppingCart class="mx-auto h-7 w-7" aria-hidden="true" />
              <p class="mt-2 font-bold">Delivery</p>
              <p class="text-xs">{{ product.fulfillment?.delivery_message ?? 'Check address' }}</p>
            </div>
          </div>
        </section>
      </aside>
    </section>
  </main>
</template>
