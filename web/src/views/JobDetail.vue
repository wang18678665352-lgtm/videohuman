<template>
  <div v-loading="loading">
    <div class="bar">
      <h2>
        {{ id }}
        <el-tag :type="statusType(job.status)" style="margin-left: 8px">{{ statusText(job.status) }}</el-tag>
        <span v-if="job.queue_position" class="queue-pos">排队第 {{ job.queue_position }} 位</span>
      </h2>
      <div>
        <el-button v-if="active" type="warning" plain :disabled="job.status === 'cancelling'" @click="cancel">
          取消任务
        </el-button>
        <el-select v-model="rerunStage" placeholder="整体重跑" style="width: 160px; margin-right: 8px" clearable>
          <el-option v-for="s in order" :key="s" :label="`只跑 ${s}`" :value="s" />
        </el-select>
        <el-button type="warning" @click="rerun(false)" :disabled="active">
          {{ rerunStage ? `重跑 ${rerunStage}` : '续跑未完成环节' }}
        </el-button>
        <el-button type="danger" @click="rerun(true)" :disabled="active">全部重跑</el-button>
      </div>
    </div>
    <el-alert v-if="job.error" :title="job.error" type="error" :closable="false" style="margin-bottom: 16px" />

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card header="环节进度">
          <div v-for="s in order" :key="s" class="stage-row">
            <el-tag :type="stageTag(s)" size="small" class="stage-tag">{{ stageStatus(s) }}</el-tag>
            <span class="stage-name">{{ s }}</span>
            <span class="stage-dur">{{ stageDur(s) }}</span>
            <el-tooltip v-if="stageError(s)" :content="stageError(s)">
              <span class="stage-err">错误详情</span>
            </el-tooltip>
          </div>
        </el-card>
        <el-card :header="job.workflow ? '最终文案' : '文案'" style="margin-top: 16px" v-if="rewriteText">
          <div class="rewrite-text">{{ rewriteText }}</div>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card header="成品" v-if="job.final">
          <video :src="fileUrl(id, job.final)" controls class="final-video" />
          <div v-if="job.cover" style="margin-top: 12px">
            <el-image :src="fileUrl(id, job.cover)" fit="contain" style="max-height: 200px" />
          </div>
          <div style="margin-top: 8px"><el-link :href="fileUrl(id, job.final)" type="primary" download>下载成片</el-link></div>
        </el-card>
        <el-card header="中间产物" style="margin-top: 16px" v-if="midOutputs.length">
          <div v-for="m in midOutputs" :key="m.label" class="mid-row">
            <span class="mid-label">{{ m.label }}</span>
            <audio v-if="m.kind === 'audio'" :src="m.url" controls style="height: 32px" />
            <video v-else-if="m.kind === 'video'" :src="m.url" controls class="mid-video" />
            <el-image v-else-if="m.kind === 'image'" :src="m.url" fit="contain" style="max-height: 120px" />
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
import {
  STATUS_TEXT, STATUS_TYPE, assetUrl, cancelJob, fetchText, fileUrl, getJob, getSettings, isActive, runJob,
} from '../api.js'

const props = defineProps({ id: String })
const job = ref({ stages: {} })
const loading = ref(true)
const stageNames = ref(['download', 'transcribe', 'rewrite', 'tts', 'avatar', 'postprocess', 'publish'])
const rerunStage = ref('')
const rewriteText = ref('')
const publishResult = ref('')
let timer = null

const active = computed(() => isActive(job.value.status))
const order = computed(() => job.value.stage_order || stageNames.value)
const KIND_BY_EXT = { wav: 'audio', mp3: 'audio', m4a: 'audio', mp4: 'video', mov: 'video', png: 'image', jpg: 'image' }
// 中间产物:按节点顺序列出所有产物(成品与封面在上方单独展示)
const midOutputs = computed(() => {
  const st = job.value.manifest?.stages || {}
  const spec = job.value.spec || {}
  const list = []
  if (spec.audio) {
    list.push({ label: '驱动音频', kind: 'audio', path: spec.audio, url: assetUrl(spec.audio) })
  }
  for (const name of order.value) {
    for (const [key, path] of Object.entries(st[name]?.outputs || {})) {
      if (path === job.value.final || path === job.value.cover) continue
      const kind = KIND_BY_EXT[path.split('.').pop().toLowerCase()] || 'link'
      list.push({ label: `${name} · ${key}`, kind, path, url: fileUrl(props.id, path) })
    }
  }
  return list
})

// 展示的文案:经典流程取 rewrite;工作流取成品所在节点上游最近的文本产物(翻译/润色结果)
function textOutputPath() {
  const st = job.value.manifest?.stages || {}
  if (!job.value.workflow) return 'rewrite/text.txt'
  const texts = order.value.flatMap((n) => Object.values(st[n]?.outputs || {}).filter((p) => p.endsWith('/text.txt')))
  return texts[texts.length - 1]
}

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
function statusType(s) { return STATUS_TYPE[s] || 'info' }
function statusText(s) { return STATUS_TEXT[s] || s }

async function refresh() {
  try {
    job.value = await getJob(props.id)
  } catch (e) {
    ElMessage.error(`刷新任务失败: ${e.message}`)
  }
  clearTimeout(timer)
  timer = setTimeout(refresh, active.value ? 2000 : 15000)
}

async function rerun(noResume) {
  const body = { resume: !noResume }
  if (rerunStage.value && !noResume) body.only = rerunStage.value
  try {
    await runJob(props.id, body)
    ElMessage.success('已入队')
  } catch (e) {
    ElMessage.error(e.message)
  }
  refresh()
}

async function cancel() {
  try {
    const r = await cancelJob(props.id)
    ElMessage.success(r.status === 'cancelled' ? '已取消' : '已请求取消,当前环节完成后停止')
  } catch (e) {
    ElMessage.error(e.message)
  }
  refresh()
}

onMounted(async () => {
  try {
    const s = await getSettings()
    stageNames.value = s.stage_names
    await refresh()
    const textPath = textOutputPath()
    if (textPath) fetchText(props.id, textPath).then((t) => { rewriteText.value = t }).catch(() => {})
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
.queue-pos { margin-left: 8px; font-size: 13px; font-weight: normal; color: #909399; }
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
