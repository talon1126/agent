export interface FlashSale {
  id: number
  item_id: string
  item_price?: number | null
  sale_price: number
  stock_limit: number
  stock_remaining: number | null
  status: string
  starts_at: string
  ends_at: string
}

export interface FlashSaleListParams {
  status?: string
  limit?: number
}

export interface FlashSaleListResponse {
  ok: true
  count: number
  flash_sales: FlashSale[]
}

export interface FlashSaleResponse {
  ok: true
  flash_sale: FlashSale
}

export interface FlashSalePurchaseRequest {
  user_id: number
  shipping_address: string
  delivery_provider_id: string
}

export interface FlashSalePurchaseResponse {
  ok: true
  claim: {
    flash_sale_id: number
    user_id: number
    item_id?: string
    status: string
    order_id?: string
  }
  order: {
    order_id: string
    status: string
    customer_id?: string
  }
  items: Record<string, unknown>[]
}
