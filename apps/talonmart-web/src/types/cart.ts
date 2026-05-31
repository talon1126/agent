export interface CartItem {
  id: number
  user_id: number
  item_id: string
  item_name: string
  price: number | string
  quantity: number
}

export interface CartResponse {
  ok: true
  user_id: number
  count: number
  items: CartItem[]
}

export interface AddCartItemRequest {
  user_id: number
  item_id: string
  item_name: string
  price: number
  quantity?: number
}

export interface AddCartItemResponse {
  ok: true
  item: CartItem
}

export interface RemoveCartItemResponse {
  ok: true
  removed: boolean
  user_id: number
  item_id: string
}
