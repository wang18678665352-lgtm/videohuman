<template>
  <div v-loading="loading">
    <div class="bar">
      <h2>
        {{ id }}
        <el-tag :type="statusType(job.status)" style="margin-left: 8px">{{ statusText(job.status) }}</el-tag>
      </h2>
      <div>
        <el-select v-model="rerunStage" placeholder="整体重跑" style="width: 160px; margin-right: 8px" clearable>
          <el-option v-for="s in stageNames" :key="s" :label="`只跑 ${s}`" :value="s" />
        </el-select>
        <el-button type="warning" @click="rerun(false)" :disabled="job.status === 'running' || job.status === 'queued'">
          {{ rerunStage ? `重跑 ${rerunStage}` : '续跑未完成环节' }}
        </el-button>
        <el-button type="danger" @click="rerun(true)" :disabled="job.status === 'running' || job.status === 'queued'">全部重跑</el-button>
      </div>
    </div>
    <el-alert v-if="job.error" :title="job.error" type="error" :closable="false" style="margin-bottom: 16px" />

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card header="环节进度">
          <div v-for="s in stageNames" :key="s" class="stage-row">
            <el-tag :type="stageTag(s)" size="small" class="stage-tag">{{ stageStatus(s) }}</el-tag>
            <span class="stage-name">{{ s }}</span>
            <span class="stage-dur">{{ stageDur(s) }}</span>
            <el-tooltip v-if="stageError(s)" :content="stageError(s)">
              <span class="stage-err">错误详情</span>
            </el-tooltip>
          </div>
        </el-card>
        <el-card header="文案" style="margin-top: 16px" v-if="rewriteText">
          <div class="rewrite-text">{{ rewriteText }}</div>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card header="成品" v-if="outputs.final">
          <video :src="fileUrl(id, outputs.final)" controls class="final-video" />
          <div v-if="outputs.cover" style="margin-top: 12px">
            <el-image :src="fileUrl(id, outputs.cover)" fit="contain" style="max-height: 200px" />
          </div>
        </el-card>
        <el-card header="中间产物" style="margin-top: 16px" v-if="midOutputs.length">
          <div v-for="m in midOutputs" :key="m.label" class="mid-row">
            <span class="mid-label">{{ m.label }}</span>
            <audio v-if="m.kind === 'audio'" :src="m.url" controls style="height: 32px" />
            <video v-else-if="m.kind === 'video'" :src="m.url" controls class="mid-video" />
            <el-link v-else :href="m.url" target="_blank" type="primary">{{ m.path }}</el-link>
          </div>
        </el-card>
        <el-card header="发布结果" style="margin-top: 16px" v-if="publishResult">
          <pre class="publish-json">{{ publishResult }}</pre>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { assetUrl, fetchText, fileUrl, getJob, getSettings, runJob } from '../api.js'

const props = defineProps({ id: String })
const job = ref({ stages: {} })
const loading = ref(true)
const stageNames = ref(['download', 'transcribe', 'rewrite', 'tts', 'avatar', 'postprocess', 'publish'])
const rerunStage = ref('')
const rewriteText = ref('')
const publishResult = ref('')
let timer = null

const outputs = computed(() => job.value.manifest?.stages?.postprocess?.outputs || {})
const midOutputs = computed(() => {
  const st = job.value.manifest?.stages || {}
  const spec = job.value.spec || {}
  const list = []
  if (spec.audio) {
    list.push({ label: '驱动音频', kind: 'audio', path: spec.audio, url: assetUrl(spec.audio) })
  } else if (st.tts?.outputs?.speech) {
    list.push({ label: 'TTS 音频', kind: 'audio', path: st.tts.outputs.speech, url: fileUrl(props.id, st.tts.outputs.speech) })
  }
  if (st.download?.outputs?.video) {
    list.push({ label: '原片', kind: 'link', path: st.download.outputs.video, url: fileUrl(props.id, st.download.outputs.video) })
  }
  if (st.avatar?.outputs?.avatar) {
    list.push({ label: '数字人成片(未后期)', kind: 'video', path: st.avatar.outputs.avatar, url: fileUrl(props.id, st.avatar.outputs.avatar) })
  }
  return list
})

function stage(name) { return job.value.manifest?.stages?.[name] }
function stageStatus(name) { return stage(name)?.status || job.value.stages?.[name] || 'pending' }
function stageTag(name) {
  return { success: 'success', skipped: 'info', failed: 'danger', running: '' }[stageStatus(name)] || 'info'
}
function stageDur(name) {
  const s = stage(name)
  if (!s?.started_at || !s?.finished_at) return ''
  const d = (new Date(s.finished_at) - new Date(s.started_at)) / 1000
  return `${d.toFixed(1)}s`
}
function stageError(name) { return stage(name)?.error || '' }
function statusType(s) { return { done: 'success', failed: 'danger', running: '', queued: 'info' }[s] || 'info' }
function statusText(s) {
  return { done: '完成', failed: '失败', running: '运行中', queued: '排队中', interrupted: '中断' }[s] || s
}

async function refresh() {
  job.value = await getJob(props.id)
  const active = job.value.status === 'running' || job.value.status === 'queued'
  clearTimeout(timer)
  timer = setTimeout(refresh, active ? 2000 : 15000)
}

async function rerun(noResume) {
  const body = { resume: !noResume }
  if (rerunStage.value && !noResume) body.only = rerunStage.value
  await runJob(props.id, body)
  ElMessage.success('已入队')
  refresh()
}

onMounted(async () => {
  try {
    const s = await getSettings()
    stageNames.value = s.stage_names
    await refresh()
    fetchText(props.id, 'rewrite/text.txt').then((t) => { rewriteText.value = t }).catch(() => {})
    fetchText(props.id, 'publish/result.json')
      .then((t) => { publishResult.value = JSON.stringify(JSON.parse(t), null, 2) }).catch(() => {})
  } finally {
    loading.value = false
  }
})
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.bar h2 { margin: 0; display: flex; align-items: center; }
.stage-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.stage-tag { width: 76px; text-align: center; }
.stage-name { font-family: monospace; width: 110px; }
.stage-dur { color: #909399; font-size: 12px; }
.stage-err { color: #f56c6c; font-size: 12px; cursor: help; }
.final-video { width: 100%; max-height: 560px; background: #000; }
.rewrite-text { white-space: pre-wrap; line-height: 1.8; }
.mid-row { display: flex; align-items: center; gap: 12px; padding: 6px 0; }
.mid-video { width: 100%; max-height: 320px; background: #000; }
.mid-label { width: 140px; color: #606266; }
.publish-json { white-space: pre-wrap; font-size: 12px; margin: 0; }
</style>
