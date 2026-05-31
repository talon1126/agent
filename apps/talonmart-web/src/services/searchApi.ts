import { apiClient } from '@/services/apiClient'
import type { SearchResponse } from '@/types/search'

export async function searchProducts(query: string): Promise<SearchResponse> {
  const response = await apiClient.get<SearchResponse>('/search', {
    params: { q: query },
  })

  return response.data
}
