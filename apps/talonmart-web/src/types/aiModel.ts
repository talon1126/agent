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

export interface AiModelChatResponse {
  conversation_id?: string | null
  answer: string
  recommended_links: AiModelRecommendedLink[]
}
