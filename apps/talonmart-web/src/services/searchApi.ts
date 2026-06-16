import { apiClient } from '@/services/apiClient'
import type { SearchResponse } from '@/types/search'

export async function searchProducts(query: string): Promise<SearchResponse> {
  const response = await apiClient.get<SearchResponse>('/search', {
    params: { q: query },
  })

  return response.data
}

/**
 * Fetch products for a Departments guide route.
 *
 * The category parameter is sent separately from `q` so category browsing does
 * not depend on keyword matching or category-id search text.
 */
export async function searchProductsByCategory(category: string): Promise<SearchResponse> {
  const response = await apiClient.get<SearchResponse>('/search', {
    params: { category },
  })

  return response.data
}
