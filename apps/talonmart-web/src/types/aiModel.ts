export interface AiModelChatRequest {
  conversation_id?: string
  message: string
  links: string[]
}

export interface AiModelRecommendedLink {
  item_id: string
  item_name: string
  url: string
}

export interface AiModelToolResult {
  tool: string
  ok: boolean
  input: string
  item_id?: string | null
  data?: Record<string, unknown>
  error?: string | null
}

export interface AiModelChatResponse {
  conversation_id?: string | null
  answer: string
  recommended_links: AiModelRecommendedLink[]
  tool_results: AiModelToolResult[]
}
