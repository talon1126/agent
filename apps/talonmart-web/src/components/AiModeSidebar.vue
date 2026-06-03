<script setup lang="ts">
import { ref } from 'vue'
import {
  ClipboardList,
  Headphones,
  MessageSquareText,
  Sparkles,
  UserCircle,
} from 'lucide-vue-next'

import AiModeChatPanel from './AiModeChatPanel.vue'

const isAiPanelOpen = ref(false)

const sideItems = [
  { label: '我的', icon: UserCircle },
  { label: '客服', icon: Headphones },
  { label: 'AI模式', icon: Sparkles, action: 'ai' },
  { label: '反馈', icon: MessageSquareText },
  { label: '调研', icon: ClipboardList },
]

function handleSideItemClick(action?: string) {
  if (action === 'ai') {
    // 中文注释：AI模式只负责打开本地对话面板，实际问答通过 ai-service AImodel 接口完成。
    isAiPanelOpen.value = true
  }
}
</script>

<template>
  <div class="ai-mode-shell" aria-label="TalonMart sidebar">
    <nav class="ai-mode-sidebar" aria-label="Quick actions">
      <button
        v-for="item in sideItems"
        :key="item.label"
        class="ai-mode-sidebar__item"
        type="button"
        :aria-label="item.action === 'ai' ? 'Open AI mode' : item.label"
        :class="{ 'ai-mode-sidebar__item--active': item.action === 'ai' && isAiPanelOpen }"
        @click="handleSideItemClick(item.action)"
      >
        <span class="ai-mode-sidebar__icon">
          <component :is="item.icon" :size="29" stroke-width="2.3" />
        </span>
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <AiModeChatPanel v-if="isAiPanelOpen" @close="isAiPanelOpen = false" />
  </div>
</template>

<style scoped>
.ai-mode-shell {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 40;
}

.ai-mode-sidebar,
.ai-mode-sidebar__item,
.ai-mode-sidebar__icon {
  display: flex;
  align-items: center;
}

.ai-mode-sidebar {
  pointer-events: auto;
  position: fixed;
  top: 50%;
  right: 14px;
  z-index: 44;
  width: 52px;
  transform: translateY(-50%);
  flex-direction: column;
  gap: 6px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 18px 46px rgba(16, 24, 40, 0.16);
  padding: 10px 4px;
}

.ai-mode-sidebar__item {
  width: 44px;
  min-height: 58px;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #4b5563;
  cursor: pointer;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.15;
  padding: 5px 2px;
}

.ai-mode-sidebar__item:hover,
.ai-mode-sidebar__item--active {
  background: #fff0f3;
  color: #101828;
}

.ai-mode-sidebar__item--active svg {
  color: #ff5a72;
}

.ai-mode-sidebar__icon {
  position: relative;
  justify-content: center;
  color: #111827;
}

@media (max-width: 720px) {
  .ai-mode-sidebar {
    right: 8px;
    width: 48px;
    padding: 8px 3px;
  }

  .ai-mode-sidebar__item {
    width: 42px;
    min-height: 54px;
    font-size: 12px;
  }
}
</style>
