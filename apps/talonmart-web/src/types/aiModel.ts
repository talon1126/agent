export interface AiModelChatRequest {
  user_id: number
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

export interface AiModelConversationSummary {
  id: number
  title?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AiModelStoredMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  links: string[]
  recommended_links: AiModelRecommendedLink[]
  created_at?: string | null
}
