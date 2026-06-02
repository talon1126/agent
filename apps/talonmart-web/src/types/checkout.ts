import type { CartItem } from '@/types/cart'

export interface DeliveryAddress {
  id: number
  user_id: number
  receiver_name: string
  phone_number: string
  address: string
  is_default: 0 | 1
}

export interface DeliveryAddressResponse {
  ok: true
  user_id: number
  count: number
  items: DeliveryAddress[]
}

export interface WarehouseOrderItemRequest {
  item_id: string
  quantity: number
}

export interface CreateWarehouseOrderRequest {
  customer_id: string
  delivery_provider_id: string
  courier_phone: string
  shipping_address: string
  items: WarehouseOrderItemRequest[]
  created_by: string
}

export interface WarehouseOrderResponse {
  ok: true
  order: {
    order_id: string
    customer_id: string
    status: string
  } & Record<string, unknown>
  items: CartItem[] | Record<string, unknown>[]
}
