import { apiClient } from '@/services/apiClient'
import type {
  CategoryRankingParams,
  CategoryRankingResponse,
  HomeHotRankingResponse,
} from '@/types/categoryRanking'

export async function fetchCategoryRanking(
  categoryId: string,
  params: CategoryRankingParams = {},
): Promise<CategoryRankingResponse> {
  const response = await apiClient.get<CategoryRankingResponse>(
    `/rankings/categories/${categoryId}`,
    { params },
  )

  return response.data
}

export async function fetchHomeHotRankings(
  params: CategoryRankingParams = {},
): Promise<HomeHotRankingResponse> {
  const response = await apiClient.get<HomeHotRankingResponse>('/rankings/home/hot', {
    params,
  })

  return response.data
}
