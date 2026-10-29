<template>
  <el-form-item :label="param.title" :required="param.required">
    <el-switch v-if="param.kind === 'bool'" :model-value="current" @update:model-value="emitValue" />
    <el-input-number v-else-if="param.kind === 'number'" :model-value="current" :step="0.1"
      controls-position="right" @update:model-value="emitValue" />
    <el-input-number v-else-if="param.kind === 'integer'" :model-value="current" :step="1" :precision="0"
      controls-position="right" @update:model-value="emitValue" />
    <el-select v-else-if="param.kind === 'select'" :model-value="current" @update:model-value="emitValue">
      <el-option v-for="o in param.options" :key="o" :label="o" :value="o" />
    </el-select>
    <el-select v-else-if="assetOptions" :model-value="current" filterable clearable allow-create
      :placeholder="placeholder" @update:model-value="emitValue">
      <el-option v-for="a in assetOptions" :key="a" :label="a" :value="a" />
    </el-select>
    <el-input v-else-if="param.kind === 'text'" type="textarea" :rows="textRows" :model-value="current"
      :placeholder="placeholder" @update:model-value="emitValue" />
    <el-input v-else :model-value="current" :placeholder="placeholder" @update:model-value="emitValue" />
    <div v-if="param.help" class="param-help">{{ param.help }}</div>
  </el-form-item>
</template>

<script setup>
import { computed } from 'vue'

// 按后端 Param.kind 渲染输入控件;值为空时显示后端默认值
const props = defineProps({
  param: { type: Object, required: true },
  modelValue: { default: undefined },
  assets: { type: Object, default: () => ({}) },
  textRows: { type: Number, default: 3 },
})
const emit = defineEmits(['update:modelValue'])

const current = computed(() => (props.modelValue === undefined || props.modelValue === null
  ? props.param.default ?? (props.param.kind === 'bool' ? false : undefined)
  : props.modelValue))
const placeholder = computed(() => (props.param.default ? `默认:${props.param.default}` : ''))
const assetOptions = computed(() => ({
  asset_audio: props.assets.audios,
  asset_video: props.assets.videos,
  asset_dir: props.assets.dirs,
}[props.param.kind]))

function emitValue(v) {
  emit('update:modelValue', v === '' ? undefined : v)
}
</script>

<style scoped>
.param-help { width: 100%; color: var(--vh-text-3); font-size: 12px; line-height: 1.5; margin-top: 2px; }
</style>
