<script setup lang="ts">
import {
  ClipboardList,
  MapPin,
  Menu,
  PackageCheck,
  Search,
  ShoppingCart,
  UserRound,
} from 'lucide-vue-next'

const categories = ['Paper Goods', 'Dairy', 'Beverages', 'Office Supplies']

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
</script>

<template>
  <main class="min-h-screen bg-[#F5F7FA] text-[#101828]">
    <header class="bg-[#0F2A44] text-white">
      <div class="mx-auto flex max-w-[1440px] flex-wrap items-center gap-4 px-6 py-4">
        <a href="/" class="flex shrink-0 items-center gap-3" aria-label="TalonMart home">
          <span
            class="grid h-11 w-11 place-items-center rounded-md bg-[#00A6C8] font-bold text-[#0F2A44]"
          >
            TM
          </span>
          <span class="text-2xl font-bold">TalonMart</span>
        </a>

        <button
          class="flex min-h-11 shrink-0 items-center gap-2 rounded-md px-3 text-left text-sm hover:bg-white/10"
        >
          <MapPin class="h-5 w-5 text-[#00A6C8]" aria-hidden="true" />
          <span>
            <span class="block text-xs text-white/70">Deliver to</span>
            <span class="font-semibold">Hong Kong</span>
          </span>
        </button>

        <form
          class="order-3 flex min-h-11 w-full overflow-hidden rounded-md bg-white lg:order-none lg:min-w-[360px] lg:flex-1"
          role="search"
        >
          <input
            aria-label="Search products"
            class="min-w-0 flex-1 px-4 text-base text-[#101828] outline-none"
            placeholder="Search groceries, beverages, paper goods..."
            type="search"
          />
          <button
            class="grid w-14 place-items-center bg-[#00A6C8] text-[#0F2A44] transition hover:bg-[#28BEDB]"
            type="submit"
            aria-label="Submit search"
          >
            <Search class="h-5 w-5" aria-hidden="true" />
          </button>
        </form>

        <a
          class="flex min-h-11 shrink-0 items-center gap-2 rounded-md px-3 text-sm hover:bg-white/10"
          href="#"
        >
          <UserRound class="h-5 w-5 text-[#00A6C8]" aria-hidden="true" />
          Account
        </a>
        <a
          class="flex min-h-11 shrink-0 items-center gap-2 rounded-md px-3 text-sm hover:bg-white/10"
          href="#"
        >
          <ClipboardList class="h-5 w-5 text-[#00A6C8]" aria-hidden="true" />
          Orders
        </a>
        <a
          class="flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-md bg-white px-3 text-sm font-bold text-[#0F2A44]"
          href="#"
          style="color: #0f2a44"
        >
          <ShoppingCart class="h-5 w-5" aria-hidden="true" />
          Cart
        </a>
      </div>

      <nav class="border-t border-white/10 bg-[#123A5D]">
        <div
          class="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3 text-sm font-semibold"
        >
          <button class="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-white/10">
            <Menu class="h-5 w-5 text-[#00A6C8]" aria-hidden="true" />
            Departments
          </button>
          <a v-for="category in categories" :key="category" href="#" class="hover:text-[#8BE8F7]">
            {{ category }}
          </a>
        </div>
      </nav>
    </header>

    <section class="mx-auto grid max-w-[1440px] gap-5 px-6 py-6 xl:grid-cols-[280px_1fr]">
      <aside class="rounded-lg border border-[#D8E0E8] bg-white p-4">
        <h2 class="mb-3 text-base font-bold">Shop by department</h2>
        <div class="grid gap-2">
          <a
            v-for="category in categories"
            :key="category"
            class="rounded-md border border-transparent px-3 py-3 text-sm font-semibold hover:border-[#00A6C8] hover:bg-[#E6F8FB]"
            href="#"
          >
            {{ category }}
          </a>
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
              Browse focused household, office, dairy, and beverage deals backed by live order and
              inventory APIs.
            </p>
            <button
              class="mt-6 min-h-11 rounded-md bg-[#FFB020] px-5 font-bold text-[#0F2A44] hover:bg-[#FFC44D]"
            >
              Shop today deals
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
