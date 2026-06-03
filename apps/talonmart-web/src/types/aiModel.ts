export interface AiModelChatRequest {
  user_id: string
  conversation_id?: number | null
  message: string
  links: string[]
}

export interface AiModelRecommendedLink {
  item_id: string
  item_name: string
  url: string
}

export interface AiModelChatResponse {
  conversation_id?: number | null
  answer: string
  recommended_links: AiModelRecommendedLink[]
}
