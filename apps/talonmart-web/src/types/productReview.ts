export interface ItemReview {
  id: number
  item_id: string
  user_id: number
  rating: number
  title: string
  content: string
  created_at: string
  updated_at: string
}

export interface ItemReviewSummary {
  average_rating: number
  review_count: number
}

export interface ItemReviewListParams {
  limit?: number
  offset?: number
}

export interface ItemReviewListResponse {
  ok: true
  item_id: string
  count: number
  summary: ItemReviewSummary
  reviews: ItemReview[]
}

export interface ItemReviewCreateRequest {
  user_id: number
  rating: number
  title: string
  content: string
}

export interface ItemReviewCreateResponse {
  ok: true
  review: ItemReview
}
