export interface ProductImage {
  url: string
  alt?: string
  sort_order?: number
}

export interface ProductRating {
  score: number
  count: number
}

export interface ProductDetailLine {
  label: string
  value: string
}

export interface ProductFulfillment {
  shipping_available?: boolean
  pickup_available?: boolean
  delivery_available?: boolean
  pickup_message?: string
  delivery_message?: string
}

export interface ProductDetail {
  item_id: string
  item_name: string
  brand: string
  spec: string
  category_id: string
  price: number
  currency?: string
  images?: ProductImage[]
  rating?: ProductRating | null
  badges?: string[]
  features?: string[]
  ingredients?: string
  description?: string
  details?: ProductDetailLine[]
  fulfillment?: ProductFulfillment
}

export interface ProductDetailResponse {
  ok: true
  item: ProductDetail
}
