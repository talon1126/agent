<script setup lang="ts">
import { ref } from 'vue'

import AiModeChatPanel from './AiModeChatPanel.vue'

const isAiPanelOpen = ref(false)

function openAiPanel() {
  isAiPanelOpen.value = true
}
</script>

<template>
  <div class="ai-mode-shell" aria-label="TalonMart AI shopping assistant">
    <button
      class="ai-smiley-launcher"
      type="button"
      aria-label="Open AI mode"
      data-testid="ai-smiley-launcher"
      :aria-expanded="isAiPanelOpen"
      @click="openAiPanel"
    >
      <span class="ai-smiley-launcher__face" aria-hidden="true">
        <span class="ai-smiley-launcher__eye ai-smiley-launcher__eye--left"></span>
        <span class="ai-smiley-launcher__eye ai-smiley-launcher__eye--right"></span>
        <span class="ai-smiley-launcher__mouth"></span>
      </span>
    </button>

    <AiModeChatPanel v-if="isAiPanelOpen" @close="isAiPanelOpen = false" />
  </div>
</template>

<style scoped>
.ai-mode-shell {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 48;
}

.ai-smiley-launcher {
  pointer-events: auto;
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 52;
  display: grid;
  height: 76px;
  width: 76px;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: rgba(233, 241, 254, 0.92);
  box-shadow: 0 14px 30px rgba(0, 83, 226, 0.18);
  cursor: pointer;
  padding: 0;
  transition:
    transform 180ms ease,
    box-shadow 180ms ease;
  animation: ai-smiley-float 3.2s ease-in-out infinite;
}

.ai-smiley-launcher:hover {
  transform: translateY(-3px) scale(1.03);
  box-shadow: 0 18px 38px rgba(0, 83, 226, 0.26);
}

.ai-smiley-launcher__face {
  position: relative;
  display: block;
  height: 60px;
  width: 60px;
  border-radius: 50%;
  background: #ffc220;
  box-shadow:
    inset 0 -4px 0 rgba(196, 134, 0, 0.16),
    inset 0 3px 0 rgba(255, 255, 255, 0.28);
}

.ai-smiley-launcher__eye {
  position: absolute;
  top: 18px;
  height: 14px;
  width: 6px;
  border-radius: 999px;
  background: #0053e2;
  animation: ai-smiley-blink 4.6s infinite;
}

.ai-smiley-launcher__eye--left {
  left: 18px;
}

.ai-smiley-launcher__eye--right {
  right: 18px;
}

.ai-smiley-launcher__mouth {
  position: absolute;
  left: 15px;
  top: 29px;
  height: 17px;
  width: 30px;
  border-bottom: 4px solid #0053e2;
  border-radius: 0 0 999px 999px;
}

@keyframes ai-smiley-float {
  0%,
  100% {
    translate: 0 0;
  }

  50% {
    translate: 0 -5px;
  }
}

@keyframes ai-smiley-blink {
  0%,
  46%,
  50%,
  100% {
    transform: scaleY(1);
  }

  48% {
    transform: scaleY(0.12);
  }
}

@media (max-width: 720px) {
  .ai-smiley-launcher {
    right: 18px;
    bottom: 18px;
    height: 68px;
    width: 68px;
  }
}
</style>
