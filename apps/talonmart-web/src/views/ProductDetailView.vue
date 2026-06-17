<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  ChevronDown,
  Heart,
  Info,
  LoaderCircle,
  PackageCheck,
  Share2,
  ShoppingCart,
  Star,
  Truck,
} from 'lucide-vue-next'

import StoreHeader from '@/components/StoreHeader.vue'
import { addCartItem, CART_USER_ID } from '@/services/cartApi'
import { fetchFlashSales } from '@/services/flashSaleApi'
import { fetchProductDetail } from '@/services/productDetailApi'
import { createItemReview, fetchItemReviews } from '@/services/productReviewApi'
import type { FlashSale } from '@/types/flashSale'
import type { ProductDetail, ProductImage } from '@/types/productDetail'
import type { ItemReview, ItemReviewSummary } from '@/types/productReview'

const route = useRoute()

const product = ref<ProductDetail | null>(null)
const activeFlashSale = ref<FlashSale | null>(null)
const selectedImageIndex = ref(0)
const isLoading = ref(false)
const isAddingToCart = ref(false)
const errorMessage = ref('')
const cartMessage = ref('')
const cartErrorMessage = ref('')
const isZooming = ref(false)
const zoomPosition = ref({ x: 50, y: 50 })
const reviews = ref<ItemReview[]>([])
const reviewSummary = ref<ItemReviewSummary>({ average_rating: 0, review_count: 0 })
const isReviewLoading = ref(false)
const isSubmittingReview = ref(false)
const reviewMessage = ref('')
const reviewErrorMessage = ref('')
const reviewForm = ref({
  rating: 5,
  title: '',
  content: '',
})

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

const displayPrice = computed(() => activeFlashSale.value?.sale_price ?? product.value?.price ?? 0)
const originalPrice = computed(() => {
  if (!activeFlashSale.value?.item_price) {
    return null
  }

  return activeFlashSale.value.item_price > activeFlashSale.value.sale_price
    ? activeFlashSale.value.item_price
    : null
})

function formatCurrency(value: number | string | undefined) {
  return new Intl.NumberFormat('en-US', {
    currency: product.value?.currency ?? 'USD',
    style: 'currency',
  }).format(Number(value || 0))
}

function formatCount(value: number) {
  return new Intl.NumberFormat('en-US').format(value)
}

function formatReviewDate(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(parsed)
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

  // The zoom lens stores relative cursor coordinates; CSS background positioning renders the lens.
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
  activeFlashSale.value = null

  try {
    // Product detail is loaded from the documented `/ip/{item_id}` endpoint.
    const response = await fetchProductDetail(itemId.value)
    product.value = response.item
    await loadActiveFlashSale(response.item.item_id)
    await loadItemReviews(response.item.item_id)
  } catch (error) {
    product.value = null
    activeFlashSale.value = null
    reviews.value = []
    reviewSummary.value = { average_rating: 0, review_count: 0 }
    errorMessage.value =
      error instanceof Error ? error.message : 'Product detail service is unavailable.'
  } finally {
    isLoading.value = false
  }
}

async function loadActiveFlashSale(targetItemId: string) {
  try {
    const response = await fetchFlashSales({ status: 'active', limit: 100 })
    activeFlashSale.value =
      response.flash_sales.find(
        (sale) =>
          sale.item_id === targetItemId &&
          sale.item_price !== null &&
          sale.item_price !== undefined &&
          sale.sale_price < sale.item_price,
      ) ?? null
  } catch {
    activeFlashSale.value = null
  }
}

async function loadItemReviews(targetItemId = itemId.value) {
  if (!targetItemId) {
    return
  }

  isReviewLoading.value = true
  reviewErrorMessage.value = ''

  try {
    const response = await fetchItemReviews(targetItemId, { limit: 20, offset: 0 })
    reviews.value = response.reviews
    reviewSummary.value = response.summary
  } catch (error) {
    reviews.value = []
    reviewSummary.value = { average_rating: 0, review_count: 0 }
    reviewErrorMessage.value =
      error instanceof Error ? error.message : 'Unable to load customer reviews.'
  } finally {
    isReviewLoading.value = false
  }
}

async function handleCreateReview() {
  if (!product.value) {
    return
  }

  const title = reviewForm.value.title.trim()
  const content = reviewForm.value.content.trim()
  if (!title || !content) {
    reviewErrorMessage.value = 'Review title and content are required.'
    return
  }

  isSubmittingReview.value = true
  reviewMessage.value = ''
  reviewErrorMessage.value = ''

  try {
    await createItemReview(product.value.item_id, {
      user_id: CART_USER_ID,
      rating: Number(reviewForm.value.rating),
      title,
      content,
    })
    reviewForm.value = { rating: 5, title: '', content: '' }
    reviewMessage.value = 'Review submitted'
    await loadItemReviews(product.value.item_id)
  } catch (error) {
    reviewErrorMessage.value = error instanceof Error ? error.message : 'Unable to submit review.'
  } finally {
    isSubmittingReview.value = false
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
    // Cart writes still go through the cart API, so backend cart validation remains authoritative.
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
    <StoreHeader />

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

    <template v-else-if="product">
    <section class="mx-auto grid max-w-[1440px] gap-8 px-6 py-8 xl:grid-cols-[760px_1fr_360px]">
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
            <p class="text-4xl font-black">
              <span v-if="activeFlashSale">Now </span>{{ formatCurrency(displayPrice) }}
            </p>
            <p
              v-if="originalPrice !== null"
              class="text-base font-semibold text-[#667085] line-through"
            >
              {{ formatCurrency(originalPrice) }}
            </p>
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
    <section class="mx-auto max-w-[1440px] px-6 pb-12">
      <div class="border-t border-[#D8E0E8] pt-8">
        <div class="grid gap-6 lg:grid-cols-[320px_1fr]">
          <section class="rounded-lg border border-[#D8E0E8] bg-[#F7F8FA] p-6">
            <p class="text-sm font-black uppercase tracking-wide text-[#0053E2]">Customer reviews</p>
            <div class="mt-4 flex items-end gap-3">
              <p class="text-5xl font-black">{{ reviewSummary.average_rating.toFixed(1) }}</p>
              <div class="pb-1">
                <div class="flex text-[#F59E0B]">
                  <Star v-for="index in 5" :key="index" class="h-5 w-5 fill-current" aria-hidden="true" />
                </div>
                <p class="mt-1 text-sm font-semibold text-[#667085]">
                  {{ formatCount(reviewSummary.review_count) }} reviews
                </p>
              </div>
            </div>

            <form
              data-testid="item-review-form"
              class="mt-6 grid gap-4"
              @submit.prevent="handleCreateReview"
            >
              <label class="grid gap-2 text-sm font-bold">
                Rating
                <select
                  v-model.number="reviewForm.rating"
                  aria-label="Review rating"
                  class="min-h-11 rounded-md border border-[#D8E0E8] bg-white px-3 outline-none focus:border-[#0053E2]"
                >
                  <option v-for="rating in [5, 4, 3, 2, 1]" :key="rating" :value="rating">
                    {{ rating }} stars
                  </option>
                </select>
              </label>
              <label class="grid gap-2 text-sm font-bold">
                Title
                <input
                  v-model="reviewForm.title"
                  aria-label="Review title"
                  class="min-h-11 rounded-md border border-[#D8E0E8] px-3 outline-none focus:border-[#0053E2]"
                  maxlength="120"
                  type="text"
                />
              </label>
              <label class="grid gap-2 text-sm font-bold">
                Review
                <textarea
                  v-model="reviewForm.content"
                  aria-label="Review content"
                  class="min-h-28 rounded-md border border-[#D8E0E8] px-3 py-3 outline-none focus:border-[#0053E2]"
                  maxlength="2000"
                />
              </label>
              <button
                class="min-h-11 rounded-full bg-[#0053E2] px-5 font-black text-white transition hover:bg-[#003A9B] disabled:cursor-not-allowed disabled:bg-[#8CB7FF]"
                type="submit"
                :disabled="isSubmittingReview"
              >
                <span v-if="isSubmittingReview" class="inline-flex items-center gap-2">
                  <LoaderCircle class="h-5 w-5 animate-spin" aria-hidden="true" />
                  Submitting
                </span>
                <span v-else>Submit review</span>
              </button>
            </form>

            <p v-if="reviewMessage" class="mt-4 rounded-md bg-[#ECFDF3] p-3 text-sm font-bold text-[#027A48]" role="status">
              {{ reviewMessage }}
            </p>
            <p v-if="reviewErrorMessage" class="mt-4 rounded-md bg-[#FEF2F2] p-3 text-sm font-bold text-[#991B1B]" role="alert">
              {{ reviewErrorMessage }}
            </p>
          </section>

          <section>
            <div v-if="isReviewLoading" class="flex min-h-40 items-center gap-3 text-[#0053E2]">
              <LoaderCircle class="h-5 w-5 animate-spin" aria-hidden="true" />
              <span class="font-bold">Loading reviews</span>
            </div>
            <div v-else-if="reviews.length === 0" class="rounded-lg border border-dashed border-[#D8E0E8] p-8 text-[#667085]">
              No reviews yet
            </div>
            <div v-else class="grid gap-4">
              <article
                v-for="review in reviews"
                :key="review.id"
                class="rounded-lg border border-[#D8E0E8] bg-white p-5"
              >
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div class="flex items-center gap-2 text-[#F59E0B]">
                    <Star
                      v-for="index in review.rating"
                      :key="index"
                      class="h-4 w-4 fill-current"
                      aria-hidden="true"
                    />
                    <span class="text-sm font-black text-[#101828]">{{ review.rating }}.0</span>
                  </div>
                  <p class="text-sm font-semibold text-[#667085]">{{ formatReviewDate(review.created_at) }}</p>
                </div>
                <h3 class="mt-3 text-lg font-black">{{ review.title }}</h3>
                <p class="mt-2 leading-7 text-[#344054]">{{ review.content }}</p>
                <p class="mt-4 text-sm font-semibold text-[#667085]">User {{ review.user_id }}</p>
              </article>
            </div>
          </section>
        </div>
      </div>
    </section>
    </template>
  </main>
</template>
