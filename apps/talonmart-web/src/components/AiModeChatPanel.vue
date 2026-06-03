<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, MoreHorizontal, Send, X } from 'lucide-vue-next'

import { streamAiModel } from '@/services/aiModelApi'
import type { AiModelRecommendedLink } from '@/types/aiModel'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  links?: AiModelRecommendedLink[]
  status?: string
}

interface AssistantContentBlock {
  id: string
  type: 'paragraph' | 'list'
  text?: string
  items?: string[]
}

const emit = defineEmits<{
  close: []
}>()

const quickPrompts = ['居家提升幸福感好物', '换季修护必备护肤品', '如何挑选高性价比的无线耳机?', '2026早春流行穿搭']
const conversationId = ref(`web-${Date.now()}`)
const draft = ref('')
const isSending = ref(false)
const errorMessage = ref('')
const messages = ref<ChatMessage[]>([])

const canSend = computed(() => draft.value.trim().length > 0 && !isSending.value)

function extractLinks(text: string): string[] {
  // 中文注释：用户把商品链接直接粘到输入框时，前端提取链接并交给 AImodel 工具处理。
  return Array.from(text.matchAll(/https?:\/\/[^\s]+|\/items\/[^\s]+/g), (match) => match[0])
}

function updateAssistantMessage(messageId: string, patch: Partial<ChatMessage>) {
  // 中文注释：流式回调是异步触发的，通过替换数组项确保每个 delta 都能触发 Vue 重新渲染。
  messages.value = messages.value.map((chatMessage) =>
    chatMessage.id === messageId ? { ...chatMessage, ...patch } : chatMessage,
  )
}

function appendAssistantContent(messageId: string, content: string) {
  const currentMessage = messages.value.find((chatMessage) => chatMessage.id === messageId)
  updateAssistantMessage(messageId, {
    content: `${currentMessage?.content || ''}${content}`,
  })
}

function cleanInlineMarkdown(text: string): string {
  return text.replace(/\*\*(.*?)\*\*/g, '$1').trim()
}

function formatAssistantContent(content: string): AssistantContentBlock[] {
  // 中文注释：模型返回 Markdown 风格文本，前端只渲染安全的段落和列表，不直接使用 v-html。
  return content
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block, blockIndex) => {
      const lines = block
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
      const listItems = lines
        .map((line) => line.match(/^(?:[-*]|\d+\.)\s+(.+)$/)?.[1])
        .filter((line): line is string => Boolean(line))

      if (listItems.length === lines.length) {
        return {
          id: `list-${blockIndex}`,
          type: 'list',
          items: listItems.map(cleanInlineMarkdown),
        }
      }

      return {
        id: `paragraph-${blockIndex}`,
        type: 'paragraph',
        text: cleanInlineMarkdown(lines.join('\n')),
      }
    })
}

async function sendMessage(messageText = draft.value) {
  const message = messageText.trim()
  if (!message || isSending.value) {
    return
  }

  errorMessage.value = ''
  isSending.value = true
  draft.value = ''
  messages.value.push({ id: `user-${Date.now()}`, role: 'user', content: message })
  const assistantMessage: ChatMessage = {
    id: `assistant-${Date.now()}`,
    role: 'assistant',
    content: '',
    status: '正在理解问题',
  }
  messages.value.push(assistantMessage)

  try {
    const response = await streamAiModel(
      {
        conversation_id: conversationId.value,
        message,
        links: extractLinks(message),
      },
      {
        onStatus: (content) => {
          updateAssistantMessage(assistantMessage.id, { status: content })
        },
        onDelta: (content) => {
          appendAssistantContent(assistantMessage.id, content)
        },
        onDone: (doneResponse) => {
          const currentMessage = messages.value.find((chatMessage) => chatMessage.id === assistantMessage.id)
          updateAssistantMessage(assistantMessage.id, {
            status: '',
            content: doneResponse.answer || currentMessage?.content || '',
            links: doneResponse.recommended_links,
          })
        },
      },
    )
    conversationId.value = response.conversation_id || conversationId.value
  } catch (error) {
    messages.value = messages.value.filter((chatMessage) => chatMessage.id !== assistantMessage.id)
    errorMessage.value = error instanceof Error ? error.message : 'AI模式暂时不可用，请稍后再试。'
  } finally {
    isSending.value = false
  }
}

function useQuickPrompt(prompt: string) {
  // 中文注释：快捷问题直接发起一次对话，避免用户还要再次点击发送。
  void sendMessage(prompt)
}
</script>

<template>
  <aside class="ai-panel" aria-label="AI mode chat panel">
    <header class="ai-panel__header">
      <div class="ai-panel__brand">
        <Bot :size="30" stroke-width="2.4" />
        <strong>有问题，找京言</strong>
      </div>
      <div class="ai-panel__actions">
        <button class="ai-icon-button" type="button" aria-label="More AI actions">
          <MoreHorizontal :size="24" />
        </button>
        <button class="ai-icon-button" type="button" aria-label="Close AI mode" @click="emit('close')">
          <X :size="28" />
        </button>
      </div>
    </header>

    <section class="ai-panel__body" aria-live="polite">
      <div class="ai-panel__intro">
        <p class="ai-panel__hi">Hi!</p>
        <p>我是京言，你的专属 AI 购物助手，我可以为你解答各种购物问题，提供实用信息。有问题问京言~</p>
      </div>

      <div class="ai-panel__prompts" aria-label="AI quick prompts">
        <button
          v-for="prompt in quickPrompts"
          :key="prompt"
          class="ai-panel__prompt"
          type="button"
          data-testid="ai-quick-prompt"
          :disabled="isSending"
          @click="useQuickPrompt(prompt)"
        >
          {{ prompt }}
        </button>
      </div>

      <div v-if="messages.length" class="ai-panel__messages">
        <article
          v-for="message in messages"
          :key="message.id"
          class="ai-message"
          :class="`ai-message--${message.role}`"
        >
          <span v-if="message.status" class="ai-message__status">{{ message.status }}</span>
          <template v-if="message.role === 'assistant'">
            <div class="ai-message__content">
              <template v-for="block in formatAssistantContent(message.content)" :key="block.id">
                <p v-if="block.type === 'paragraph'" class="ai-message__paragraph">{{ block.text }}</p>
                <ul v-else class="ai-message__list">
                  <li v-for="item in block.items" :key="item" class="ai-message__list-item">{{ item }}</li>
                </ul>
              </template>
            </div>
          </template>
          <p v-else>{{ message.content }}</p>
          <div v-if="message.links?.length" class="ai-message__links">
            <a v-for="link in message.links" :key="link.item_id" :href="link.url">
              {{ link.item_name }}
            </a>
          </div>
        </article>
      </div>

      <p v-if="errorMessage" class="ai-panel__error">{{ errorMessage }}</p>
    </section>

    <footer class="ai-panel__composer">
      <textarea
        v-model="draft"
        aria-label="请输入你的问题"
        placeholder="请输入你的问题"
        rows="1"
        @keydown.enter.prevent="sendMessage()"
      />
      <button class="ai-panel__send" type="button" aria-label="Send AI message" :disabled="!canSend" @click="sendMessage()">
        <Send :size="26" />
      </button>
    </footer>
  </aside>
</template>

<style scoped>
.ai-panel {
  pointer-events: auto;
  position: fixed;
  top: 6px;
  right: 72px;
  z-index: 45;
  display: flex;
  width: min(420px, calc(100vw - 92px));
  height: min(680px, calc(100vh - 24px));
  flex-direction: column;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid rgba(16, 24, 40, 0.08);
  background: #ffffff;
  box-shadow: 0 24px 80px rgba(16, 24, 40, 0.18);
}

.ai-panel__header,
.ai-panel__composer {
  flex: 0 0 auto;
}

.ai-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 12px;
}

.ai-panel__brand,
.ai-panel__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ai-panel__brand {
  color: #101828;
  font-size: 21px;
  font-weight: 900;
}

.ai-panel__brand svg {
  color: #b75cff;
}

.ai-icon-button,
.ai-panel__send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: #101828;
  cursor: pointer;
}

.ai-panel__body {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 14px 18px 22px;
}

.ai-panel__intro {
  max-width: 480px;
  color: #111827;
  font-size: 16px;
  line-height: 1.55;
}

.ai-panel__hi {
  margin: 0 0 18px;
  color: #8a22ff;
  font-size: 23px;
  font-weight: 900;
}

.ai-panel__prompts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 20px;
}

.ai-panel__prompt {
  min-height: 40px;
  border: 0;
  border-radius: 8px;
  background: #f8fafc;
  color: #101828;
  cursor: pointer;
  font-size: 15px;
  font-weight: 600;
  padding: 7px 12px;
}

.ai-panel__prompt:hover {
  background: #eef4ff;
}

.ai-panel__messages {
  display: grid;
  gap: 12px;
  margin-top: 24px;
}

.ai-message {
  max-width: 86%;
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 15px;
  line-height: 1.55;
}

.ai-message p {
  margin: 0;
}

.ai-message__content {
  display: grid;
  gap: 8px;
}

.ai-message__paragraph {
  white-space: pre-line;
}

.ai-message__list {
  display: grid;
  gap: 7px;
  margin: 0;
  padding-left: 18px;
}

.ai-message__list-item {
  padding-left: 2px;
}

.ai-message__status {
  display: block;
  margin-bottom: 6px;
  color: #667085;
  font-size: 13px;
  font-weight: 700;
}

.ai-message--user {
  justify-self: end;
  background: #101828;
  color: #ffffff;
}

.ai-message--assistant {
  justify-self: start;
  background: #f3f6fb;
  color: #101828;
}

.ai-message__links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.ai-message__links a {
  border-radius: 999px;
  background: #ffffff;
  color: #0053e2;
  font-size: 13px;
  font-weight: 800;
  padding: 6px 10px;
  text-decoration: none;
}

.ai-panel__error {
  color: #d92d20;
  font-weight: 700;
}

.ai-panel__composer {
  display: grid;
  grid-template-columns: 1fr 52px;
  gap: 10px;
  align-items: center;
  border-top: 1px solid #eaecf0;
  padding: 12px 14px;
}

.ai-panel__composer textarea {
  max-height: 108px;
  min-height: 44px;
  resize: none;
  border: 0;
  outline: none;
  color: #101828;
  font-size: 16px;
  line-height: 1.5;
}

.ai-panel__composer textarea::placeholder {
  color: #667085;
}

.ai-panel__send {
  height: 48px;
  border-radius: 12px;
  color: #98a2b3;
}

.ai-panel__send:not(:disabled) {
  color: #0053e2;
}

@media (max-width: 720px) {
  .ai-panel {
    right: 0;
    width: 100vw;
    height: 100vh;
    border-radius: 0;
  }

  .ai-panel__brand {
    font-size: 22px;
  }

  .ai-panel__intro,
  .ai-panel__prompt,
  .ai-panel__composer textarea {
    font-size: 17px;
  }
}
</style>
