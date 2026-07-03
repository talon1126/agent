export interface CategoryRankingItem {
  rank: number
  item_id: string
  item_name: string
  brand: string
  spec: string
  category_id: string
  category_name: string
  price: number
  score: number
  rank_type?: string
  window_type?: string
  generated_at?: string
}

export interface CategoryRankingParams {
  limit?: number
  rank_type?: string
  window_type?: string
}

export interface CategoryRankingResponse {
  ok: true
  category_id: string
  rank_type: string
  window_type: string
  count: number
  items: CategoryRankingItem[]
}

export interface HomeHotRankingResponse {
  ok: true
  rank_type: string
  window_type: string
  count: number
  items: CategoryRankingItem[]
}
