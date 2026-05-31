import axios from 'axios'

import type { SearchResponse } from '@/types/search'

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || '/api'

const searchClient = axios.create({
  baseURL: apiBaseUrl,
  timeout: 10_000,
})

export async function searchProducts(query: string): Promise<SearchResponse> {
  const response = await searchClient.get<SearchResponse>('/search', {
    params: { q: query },
  })

  return response.data
}
