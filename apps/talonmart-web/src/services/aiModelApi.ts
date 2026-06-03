import type { AiModelChatRequest, AiModelChatResponse } from '@/types/aiModel'

interface AiModelStreamHandlers {
  onStatus?: (content: string) => void
  onDelta?: (content: string) => void
  onDone?: (response: AiModelChatResponse) => void
}

const aiServiceBaseUrl = (import.meta.env.VITE_AI_SERVICE_BASE_URL?.trim() || '/ai-service').replace(/\/$/, '')
const aiModelUserIdStorageKey = 'aimodel_user_id'

export function getOrCreateAiModelUserId(): string {
  const existingUserId = localStorage.getItem(aiModelUserIdStorageKey)
  if (existingUserId) {
    return existingUserId
  }

  // 中文注释：AImodel 长期偏好记忆按匿名用户 ID 归属；没有登录系统时用 localStorage 稳定标识同一浏览器用户。
  const randomId =
    globalThis.crypto?.randomUUID?.() ||
    `${Date.now()}-${Math.random().toString(16).slice(2)}`
  const userId = `anon_${randomId}`
  localStorage.setItem(aiModelUserIdStorageKey, userId)
  return userId
}

export async function streamAiModel(
  request: AiModelChatRequest,
  handlers: AiModelStreamHandlers = {},
): Promise<AiModelChatResponse> {
  // 中文注释：AImodel 属于 ai-service，不能走 mock-api 的 /api 代理，否则会在 mock-api 返回 404。
  const response = await fetch(`${aiServiceBaseUrl}/AImodel/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(await response.text())
  }
  if (!response.body) {
    throw new Error('AImodel stream is empty.')
  }

  const decoder = new TextDecoder()
  const reader = response.body.getReader()
  let buffer = ''
  let finalResponse: AiModelChatResponse | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split(/\r?\n\r?\n/)
    buffer = events.pop() || ''
    for (const eventText of events) {
      finalResponse = handleStreamEvent(eventText, handlers, finalResponse)
    }
  }

  if (buffer.trim()) {
    finalResponse = handleStreamEvent(buffer, handlers, finalResponse)
  }
  if (!finalResponse) {
    throw new Error('AImodel stream ended without a final response.')
  }

  return finalResponse
}

function handleStreamEvent(
  eventText: string,
  handlers: AiModelStreamHandlers,
  currentResponse: AiModelChatResponse | null,
): AiModelChatResponse | null {
  const lines = eventText.split(/\r?\n/)
  const eventName = lines.find((line) => line.startsWith('event:'))?.replace('event:', '').trim()
  const dataText = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.replace('data:', '').trim())
    .join('\n')

  if (!eventName || !dataText) {
    return currentResponse
  }

  const data = JSON.parse(dataText)
  if (eventName === 'status') {
    handlers.onStatus?.(data.content)
    return currentResponse
  }
  if (eventName === 'delta') {
    handlers.onDelta?.(data.content)
    return currentResponse
  }
  if (eventName === 'error') {
    throw new Error(data.content)
  }
  if (eventName === 'done') {
    handlers.onDone?.(data)
    return data
  }

  return currentResponse
}
