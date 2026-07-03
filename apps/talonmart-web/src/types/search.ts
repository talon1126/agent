export interface InventoryBalance {
  id: number
  warehouse_id: string
  item_id: string
  quantity_on_hand: number
  storage_status: string
}

export interface SearchProduct {
  item_id: string
  item_name: string
  brand: string
  spec: string
  category_id: string
  price: number
  rating?: {
    score: number
    count: number
  } | null
  balances: InventoryBalance[]
}

export interface SearchResponse {
  ok: true
  query: string
  category?: string
  count: number
  items: SearchProduct[]
}
