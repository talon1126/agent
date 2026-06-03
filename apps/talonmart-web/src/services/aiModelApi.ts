import axios from 'axios'
import type { AiModelChatRequest, AiModelChatResponse } from '@/types/aiModel'

const aiServiceBaseUrl = import.meta.env.VITE_AI_SERVICE_BASE_URL?.trim() || '/ai-service'

const aiModelClient = axios.create({
  baseURL: aiServiceBaseUrl,
  timeout: 30_000,
})

export async function askAiModel(request: AiModelChatRequest): Promise<AiModelChatResponse> {
  // 中文注释：AImodel 属于 ai-service，不能走 mock-api 的 /api 代理，否则会在 mock-api 返回 404。
  const response = await aiModelClient.post<AiModelChatResponse>('/AImodel/chat', request)
  return response.data
}
