import { apiClient } from '@/services/apiClient'
import type {
  ItemReviewCreateRequest,
  ItemReviewCreateResponse,
  ItemReviewListParams,
  ItemReviewListResponse,
} from '@/types/productReview'

export async function fetchItemReviews(
  itemId: string,
  params: ItemReviewListParams = {},
): Promise<ItemReviewListResponse> {
  // 中文注释：评论列表按商品详情页加载，分页参数保留给后续“查看更多”。
  const response = await apiClient.get<ItemReviewListResponse>(`/items/${itemId}/reviews`, {
    params,
  })

  return response.data
}

export async function createItemReview(
  itemId: string,
  payload: ItemReviewCreateRequest,
): Promise<ItemReviewCreateResponse> {
  // 中文注释：评论时间由后端生成，前端只提交用户、评分、标题和正文。
  const response = await apiClient.post<ItemReviewCreateResponse>(
    `/items/${itemId}/reviews`,
    payload,
  )

  return response.data
}
