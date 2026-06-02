import { apiClient } from '@/services/apiClient'
import type { ProductDetailResponse } from '@/types/productDetail'

export async function fetchProductDetail(itemId: string): Promise<ProductDetailResponse> {
  // 中文注释：商品详情后端接口按文档固定为 /ip/{item_id}，前端路由不直接暴露后端路径。
  const response = await apiClient.get<ProductDetailResponse>(`/ip/${itemId}`)

  return response.data
}
