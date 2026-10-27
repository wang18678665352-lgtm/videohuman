<template>
  <div class="step-node" :class="{ selected, invalid: !meta }">
    <div class="head" :style="{ background: headColor }">
      <span class="name">{{ data.title || meta?.title || data.stepType }}</span>
      <span class="type">{{ meta ? meta.title : `未知步骤 ${data.stepType}` }}</span>
    </div>
    <div class="body">
      <div v-for="p in meta?.inputs || []" :key="`in-${p.name}`" class="port in"
        :title="`${p.title} · ${kindLabel(p.kind)}${p.required ? ' · 必需' : ''}`">
        <Handle :id="p.name" type="target" :position="Position.Left" class="handle"
          :style="{ background: kindColor(p.kind) }" />
        <span>{{ p.title }}<b v-if="p.required" class="req">*</b></span>
      </div>
      <div v-for="p in meta?.outputs || []" :key="`out-${p.name}`" class="port out"
        :title="`${p.title} · ${kindLabel(p.kind)}`">
        <span>{{ p.title }}</span>
        <Handle :id="p.name" type="source" :position="Position.Right" class="handle"
          :style="{ background: kindColor(p.kind) }" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { KIND_META } from '../api.js'

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true }, // { stepType, title, params, meta }
  selected: { type: Boolean, default: false },
})

const CATEGORY_COLOR = { 输入: '#5b8ff9', 文本: '#7262fd', 音频: '#f6903d', 视频: '#1e9493', 处理: '#8c8c8c', 输出: '#e8684a' }
const meta = computed(() => props.data.meta)
const headColor = computed(() => CATEGORY_COLOR[meta.value?.category] || '#8c8c8c')
const kindColor = (k) => KIND_META[k]?.color || '#909399'
const kindLabel = (k) => KIND_META[k]?.label || k
</script>

<style scoped>
.step-node {
  width: 190px; background: #fff; border: 1px solid #e3e5ee; border-radius: 10px;
  font-size: 12px; box-shadow: 0 2px 8px rgba(20, 22, 50, 0.08);
}
.step-node.selected { border-color: #5b5bf0; box-shadow: 0 0 0 3px rgba(91, 91, 240, 0.25), 0 6px 16px rgba(20, 22, 50, 0.12); }
.step-node.invalid { border-color: #ef4444; }
.head { color: #fff; padding: 7px 11px; border-radius: 9px 9px 0 0; display: flex; flex-direction: column; }
.name { font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.type { opacity: 0.85; font-size: 11px; }
.body { padding: 4px 0; }
.port { position: relative; padding: 3px 12px; color: #5b5f73; }
.port.out { text-align: right; }
.req { color: #f56c6c; margin-left: 2px; }
.handle { width: 10px; height: 10px; border: 2px solid #fff; }
</style>
