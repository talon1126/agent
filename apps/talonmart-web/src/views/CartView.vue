<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  ChevronDown,
  Gift,
  Heart,
  Info,
  LoaderCircle,
  MapPin,
  Minus,
  Plus,
  Search,
  ShoppingCart,
  Truck,
  UserRound,
} from 'lucide-vue-next'

import { addCartItem, CART_USER_ID, fetchCart, removeCartItem } from '@/services/cartApi'
import { createWarehouseOrder, fetchDeliveryAddresses } from '@/services/checkoutApi'
import type { CartItem } from '@/types/cart'
import type { DeliveryAddress } from '@/types/checkout'

const router = useRouter()

const cartItems = ref<CartItem[]>([])
const deliveryAddresses = ref<DeliveryAddress[]>([])
const selectedAddressId = ref<number | null>(null)
const isLoading = ref(false)
const isAddressLoading = ref(false)
const isCheckingOut = ref(false)
const errorMessage = ref('')
const addressErrorMessage = ref('')
const checkoutErrorMessage = ref('')
const pendingItemId = ref('')
const searchQuery = ref('')

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

const essentials = [
  {
    label: 'Disinfecting wipes',
    badge: 'Rollback',
    image:
      'https://images.unsplash.com/photo-1584467735871-8297327a613a?auto=format&fit=crop&w=260&q=80',
  },
  {
    label: 'Protein drink',
    badge: 'Best seller',
    image:
      'https://images.unsplash.com/photo-1593095948071-474c5cc2989d?auto=format&fit=crop&w=260&q=80',
  },
  {
    label: 'Flushable wipes',
    badge: 'Best seller',
    image:
      'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=260&q=80',
  },
  {
    label: 'Laundry detergent',
    badge: 'Best seller',
    image:
      'https://images.unsplash.com/photo-1626806787461-102c1bfaaea1?auto=format&fit=crop&w=260&q=80',
  },
]

const cartQuantity = computed(() =>
  cartItems.value.reduce((total, item) => total + Number(item.quantity || 0), 0),
)

const subtotal = computed(() =>
  cartItems.value.reduce(
    (total, item) => total + Number(item.price || 0) * Number(item.quantity || 0),
    0,
  ),
)

const shippingFee = computed(() => (cartItems.value.length > 0 && subtotal.value < 35 ? 6.99 : 0))
const estimatedTotal = computed(() => subtotal.value + shippingFee.value)
const selectedAddress = computed(
  () => deliveryAddresses.value.find((address) => address.id === selectedAddressId.value) ?? null,
)

function formatCurrency(value: number | string) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(Number(value || 0))
}

function itemImage(item: CartItem) {
  if (item.item_id.includes('milk')) {
    return 'https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=320&q=80'
  }
  if (item.item_id.includes('cola')) {
    return 'https://images.unsplash.com/photo-1622483767028-3f66f32aef97?auto=format&fit=crop&w=320&q=80'
  }
  if (item.item_id.includes('paper')) {
    return 'https://images.unsplash.com/photo-1586075010923-2dd4570fb338?auto=format&fit=crop&w=320&q=80'
  }
  return 'https://images.unsplash.com/photo-1583947581924-860bda6a26df?auto=format&fit=crop&w=320&q=80'
}

async function loadCart() {
  isLoading.value = true
  errorMessage.value = ''

  try {
    const response = await fetchCart(CART_USER_ID)
    cartItems.value = response.items
  } catch (error) {
    cartItems.value = []
    errorMessage.value =
      error instanceof Error ? error.message : 'Cart service is unavailable. Check the cart API.'
  } finally {
    isLoading.value = false
  }
}

async function loadDeliveryAddresses() {
  isAddressLoading.value = true
  addressErrorMessage.value = ''

  try {
    const response = await fetchDeliveryAddresses(CART_USER_ID)
    deliveryAddresses.value = response.items

    // 中文注释：默认地址优先；没有默认地址时选第一条，保证用户仍可直接结算。
    const defaultAddress = response.items.find((address) => Number(address.is_default) === 1)
    selectedAddressId.value = defaultAddress?.id ?? response.items[0]?.id ?? null
  } catch (error) {
    deliveryAddresses.value = []
    selectedAddressId.value = null
    addressErrorMessage.value =
      error instanceof Error
        ? error.message
        : 'Delivery address service is unavailable. Check the address API.'
  } finally {
    isAddressLoading.value = false
  }
}

async function addOne(item: CartItem) {
  pendingItemId.value = item.item_id
  errorMessage.value = ''

  try {
    await addCartItem({
      user_id: CART_USER_ID,
      item_id: item.item_id,
      item_name: item.item_name,
      price: Number(item.price || 0),
      quantity: 1,
    })
    await loadCart()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Unable to add item.'
  } finally {
    pendingItemId.value = ''
  }
}

async function removeItem(item: CartItem) {
  pendingItemId.value = item.item_id
  errorMessage.value = ''

  try {
    await removeCartItem(item.item_id, CART_USER_ID)
    await loadCart()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Unable to remove item.'
  } finally {
    pendingItemId.value = ''
  }
}

async function continueToCheckout() {
  checkoutErrorMessage.value = ''

  if (cartItems.value.length === 0) {
    checkoutErrorMessage.value = 'Your cart is empty.'
    return
  }

  if (!selectedAddress.value?.address.trim()) {
    checkoutErrorMessage.value = 'Please choose a delivery address before checkout.'
    return
  }

  isCheckingOut.value = true

  try {
    // 中文注释：前端只传商品和数量，仓库选择、库位分配、库存扣减由后端订单接口负责。
    await createWarehouseOrder({
      customer_id: String(CART_USER_ID),
      delivery_provider_id: 'sf',
      courier_phone: '',
      shipping_address: selectedAddress.value.address.trim(),
      items: cartItems.value.map((item) => ({
        item_id: item.item_id,
        quantity: Number(item.quantity || 0),
      })),
      created_by: 'talonmart-web',
    })
    router.push({ name: 'home' })
  } catch (error) {
    checkoutErrorMessage.value =
      error instanceof Error ? error.message : 'Unable to create order. Check the order API.'
  } finally {
    isCheckingOut.value = false
  }
}

function submitSearch() {
  const normalized = searchQuery.value.trim()
  if (!normalized) return
  router.push({ name: 'search', query: { q: normalized } })
}

onMounted(() => {
  loadCart()
  loadDeliveryAddresses()
})
</script>

<template>
  <main class="min-h-screen bg-[#F7F8FA] text-[#101828]">
    <header class="bg-[#0053E2] text-white shadow-sm">
      <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-6 py-4">
        <RouterLink class="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-[#FFC220] font-black text-[#0053E2]" to="/">
          TM
        </RouterLink>

        <form class="flex min-h-12 flex-1 overflow-hidden rounded-full bg-white" role="search" @submit.prevent="submitSearch">
          <input
            v-model="searchQuery"
            aria-label="Search products"
            class="min-w-0 flex-1 px-6 text-base text-[#101828] outline-none"
            placeholder="Search everything at TalonMart online and in store"
            type="search"
          />
          <button class="grid w-14 place-items-center bg-[#003A9B] text-white" type="submit" aria-label="Submit search">
            <Search class="h-5 w-5" aria-hidden="true" />
          </button>
        </form>

        <RouterLink class="hidden min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex" to="/">
          <Heart class="h-5 w-5" aria-hidden="true" />
          <span>
            <span class="block text-xs">Reorder</span>
            My Items
          </span>
        </RouterLink>

        <RouterLink class="hidden min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10 lg:flex" to="/">
          <UserRound class="h-5 w-5" aria-hidden="true" />
          <span>
            <span class="block text-xs">Sign In</span>
            Account
          </span>
        </RouterLink>

        <RouterLink class="relative flex min-h-11 items-center gap-2 rounded-full px-3 text-sm font-bold hover:bg-white/10" to="/cart">
          <ShoppingCart class="h-6 w-6" aria-hidden="true" />
          <span v-if="cartQuantity" class="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-[#FFC220] px-1 text-xs font-black text-[#101828]">
            {{ cartQuantity }}
          </span>
          <span class="hidden sm:inline">{{ formatCurrency(subtotal) }}</span>
        </RouterLink>
      </div>

      <nav class="border-t border-white/15 bg-[#EAF2FF] text-[#101828]">
        <div class="mx-auto flex max-w-[1440px] gap-3 overflow-x-auto px-6 py-3">
          <button
            v-for="tab in topTabs"
            :key="tab"
            class="flex min-h-10 shrink-0 items-center gap-2 rounded-full bg-white px-5 text-sm font-bold shadow-sm"
            type="button"
          >
            {{ tab }}
            <ChevronDown v-if="tab === 'Departments' || tab === 'Services'" class="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </nav>
    </header>

    <section class="mx-auto grid max-w-[1180px] gap-6 px-6 py-8 xl:grid-cols-[1fr_360px]">
      <div class="space-y-6">
        <h1 class="text-3xl font-black">
          Cart <span class="font-normal">({{ cartQuantity }} {{ cartQuantity === 1 ? 'item' : 'items' }})</span>
        </h1>

        <div class="flex items-center gap-3">
          <span class="grid h-10 w-10 place-items-center rounded-full bg-[#D7F4FF]">
            <Truck class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
          </span>
          <h2 class="text-2xl font-black">Pickup and delivery options</h2>
          <ChevronDown class="h-6 w-6" aria-hidden="true" />
        </div>

        <section class="rounded-lg border border-[#D8E0E8] bg-white p-6 shadow-sm">
          <div class="flex items-center justify-between gap-4">
            <div class="flex items-center gap-3">
              <span class="grid h-10 w-10 place-items-center rounded-full bg-[#EAF2FF]">
                <MapPin class="h-5 w-5 text-[#0053E2]" aria-hidden="true" />
              </span>
              <div>
                <h2 class="text-xl font-black">Delivery address</h2>
                <p class="text-sm text-[#667085]">Choose where this order should ship.</p>
              </div>
            </div>
          </div>

          <div v-if="isAddressLoading" class="mt-5 flex items-center gap-3 rounded-lg bg-[#F7F8FA] p-4 text-sm font-bold text-[#0053E2]">
            <LoaderCircle class="h-5 w-5 animate-spin" aria-hidden="true" />
            Loading delivery addresses
          </div>

          <div v-else-if="addressErrorMessage" class="mt-5 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-sm text-[#991B1B]" role="alert">
            {{ addressErrorMessage }}
          </div>

          <div v-else-if="deliveryAddresses.length === 0" class="mt-5 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-sm text-[#991B1B]" role="alert">
            Please add a delivery address before checkout.
          </div>

          <div v-else class="mt-5 grid gap-3">
            <label
              v-for="address in deliveryAddresses"
              :key="address.id"
              class="grid cursor-pointer gap-3 rounded-lg border p-4 transition md:grid-cols-[auto_1fr_auto]"
              :class="selectedAddressId === address.id ? 'border-[#0053E2] bg-[#F5F9FF]' : 'border-[#D8E0E8] bg-white hover:border-[#8CB7FF]'"
            >
              <input
                v-model="selectedAddressId"
                class="mt-1 h-5 w-5 accent-[#0053E2]"
                name="delivery-address"
                type="radio"
                :value="address.id"
              />
              <span>
                <span class="block font-black">
                  {{ address.receiver_name }}
                  <span class="font-semibold text-[#667085]">{{ address.phone_number }}</span>
                </span>
                <span class="mt-1 block text-sm text-[#344054]">{{ address.address }}</span>
              </span>
              <span
                v-if="Number(address.is_default) === 1"
                class="h-fit rounded-full bg-[#EAF2FF] px-3 py-1 text-xs font-black text-[#0053E2]"
              >
                Default
              </span>
            </label>
          </div>
        </section>

        <div v-if="isLoading" class="grid min-h-[260px] place-items-center rounded-lg border border-[#D8E0E8] bg-white">
          <div class="flex items-center gap-3 text-lg font-bold text-[#0053E2]">
            <LoaderCircle class="h-6 w-6 animate-spin" aria-hidden="true" />
            Loading cart
          </div>
        </div>

        <div v-else-if="errorMessage" class="rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-6 text-[#991B1B]" role="alert">
          <h2 class="text-lg font-black">Cart request failed</h2>
          <p class="mt-2 text-sm">{{ errorMessage }}</p>
        </div>

        <div v-else-if="cartItems.length === 0" class="rounded-lg border border-[#D8E0E8] bg-white p-8">
          <h2 class="text-2xl font-black">Your cart is empty</h2>
          <p class="mt-2 text-[#667085]">Search for milk, paper, cola, or tissue to add products.</p>
          <RouterLink class="mt-5 inline-flex min-h-11 items-center rounded-full bg-[#0053E2] px-6 font-black text-white" :to="{ name: 'search', query: { q: 'milk' } }">
            Continue shopping
          </RouterLink>
        </div>

        <section v-else class="overflow-hidden rounded-lg border border-[#D8E0E8] bg-white shadow-sm">
          <div class="flex items-center gap-5 bg-[#EDF4FF] p-6">
            <span class="grid h-16 w-16 place-items-center rounded-full bg-[#D7F4FF]">
              <Truck class="h-8 w-8 text-[#0053E2]" aria-hidden="true" />
            </span>
            <div>
              <h2 class="text-2xl font-black">Shipping, arrives Tue, Jun 2</h2>
              <p class="mt-1 text-sm underline">95829</p>
            </div>
          </div>

          <article v-for="item in cartItems" :key="item.id" class="border-t border-[#D8E0E8] p-6">
            <p class="text-sm text-[#344054]">Sold and shipped by TalonMart</p>
            <p class="text-sm font-semibold text-[#0053E2]">Free shipping on orders over $35</p>

            <div class="mt-4 grid gap-5 md:grid-cols-[140px_1fr]">
              <div class="aspect-square overflow-hidden rounded-md bg-[#F1F5F9]">
                <img :alt="item.item_name" :src="itemImage(item)" class="h-full w-full object-cover" />
              </div>

              <div class="flex min-w-0 flex-col gap-4">
                <div>
                  <p class="text-3xl font-black">{{ formatCurrency(item.price) }}</p>
                  <h3 class="mt-2 text-xl leading-snug">{{ item.item_name }}</h3>
                  <p class="mt-2 flex items-center gap-2 text-sm">
                    <Info class="h-4 w-4 text-[#0053E2]" aria-hidden="true" />
                    Free 90-day returns
                  </p>
                  <p class="mt-2 flex items-center gap-2 text-sm">
                    <Gift class="h-4 w-4 text-[#0053E2]" aria-hidden="true" />
                    Gift Eligible
                  </p>
                </div>

                <div class="mt-auto flex flex-wrap items-center justify-end gap-6">
                  <button class="font-semibold underline" type="button" :disabled="pendingItemId === item.item_id" @click="removeItem(item)">
                    Remove
                  </button>
                  <button class="font-semibold underline" type="button">Save for later</button>

                  <div class="grid min-h-11 w-36 grid-cols-3 items-center rounded-full border border-[#AEB8C2] bg-white font-black">
                    <button
                      class="grid h-11 place-items-center rounded-l-full hover:bg-[#F1F5F9] disabled:cursor-not-allowed disabled:opacity-60"
                      type="button"
                      aria-label="Remove item"
                      :disabled="pendingItemId === item.item_id"
                      @click="removeItem(item)"
                    >
                      <Minus class="h-5 w-5" aria-hidden="true" />
                    </button>
                    <span class="text-center">{{ item.quantity }}</span>
                    <button
                      class="grid h-11 place-items-center rounded-r-full hover:bg-[#F1F5F9] disabled:cursor-not-allowed disabled:opacity-60"
                      type="button"
                      aria-label="Add one more"
                      :disabled="pendingItemId === item.item_id"
                      @click="addOne(item)"
                    >
                      <Plus class="h-5 w-5" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </article>
        </section>

        <section class="rounded-lg border border-[#D8E0E8] bg-white p-6">
          <h2 class="text-xl font-black">Add your essentials</h2>
          <div class="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <article v-for="item in essentials" :key="item.label" class="grid gap-3">
              <span class="w-fit rounded-md bg-[#EAF2FF] px-3 py-1 text-sm font-black text-[#0053E2]">
                {{ item.badge }}
              </span>
              <div class="aspect-square overflow-hidden rounded-lg bg-[#F1F5F9]">
                <img :alt="item.label" :src="item.image" class="h-full w-full object-cover" />
              </div>
              <p class="font-semibold">{{ item.label }}</p>
              <button class="min-h-10 rounded-full bg-[#0053E2] font-black text-white" type="button">
                + Add
              </button>
            </article>
          </div>
        </section>
      </div>

      <aside class="space-y-5">
        <section class="sticky top-6 rounded-lg border border-[#D8E0E8] bg-white p-6 shadow-sm">
          <button
            class="min-h-12 w-full rounded-full bg-[#0053E2] font-black text-white transition hover:bg-[#003A9B] disabled:cursor-not-allowed disabled:bg-[#8CB7FF]"
            type="button"
            aria-label="Continue to checkout"
            :disabled="isCheckingOut || isLoading || isAddressLoading"
            @click="continueToCheckout"
          >
            <span v-if="isCheckingOut" class="inline-flex items-center gap-2">
              <LoaderCircle class="h-5 w-5 animate-spin" aria-hidden="true" />
              Creating order
            </span>
            <span v-else>Continue to checkout</span>
          </button>
          <p v-if="checkoutErrorMessage" class="mt-3 rounded-md bg-[#FEF2F2] p-3 text-sm font-semibold text-[#991B1B]" role="alert">
            {{ checkoutErrorMessage }}
          </p>
          <p class="mt-5 text-center text-sm">
            For the best shopping experience,
            <RouterLink class="underline" to="/">sign in</RouterLink>
          </p>

          <div class="mt-6 space-y-4 border-t border-[#D8E0E8] pt-5 text-sm">
            <div class="flex justify-between gap-4">
              <span><strong>Subtotal</strong> ({{ cartQuantity }} {{ cartQuantity === 1 ? 'item' : 'items' }})</span>
              <span>{{ formatCurrency(subtotal) }}</span>
            </div>
            <div class="flex justify-between gap-4 text-[#667085]">
              <span>Shipping {{ subtotal < 35 && cartItems.length ? '(below $35 order minimum)' : '' }}</span>
              <span>{{ formatCurrency(shippingFee) }}</span>
            </div>
            <div class="flex justify-between gap-4">
              <span>Taxes</span>
              <span>Calculated at checkout</span>
            </div>
          </div>

          <div class="mt-5 flex justify-between border-t border-[#D8E0E8] pt-5 text-lg font-black">
            <span>Estimated total</span>
            <span>{{ formatCurrency(estimatedTotal) }}</span>
          </div>
        </section>

        <section class="flex items-center justify-between rounded-lg border border-[#D8E0E8] bg-white p-5 shadow-sm">
          <label class="flex items-center gap-4">
            <input class="h-6 w-6" type="checkbox" />
            <span>This order is a gift.</span>
          </label>
          <Gift class="h-8 w-8 text-[#0053E2]" aria-hidden="true" />
        </section>
      </aside>
    </section>
  </main>
</template>
