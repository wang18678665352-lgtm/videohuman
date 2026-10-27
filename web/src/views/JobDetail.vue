<template>
  <div class="page" v-loading="loading">
    <div class="page-head">
      <div>
        <el-button text class="back" @click="$router.push('/')"><el-icon><ArrowLeft /></el-icon><span>任务中心</span></el-button>
        <h1>
          <span class="mono-title">{{ id }}</span>
          <span class="status-pill" :class="statusType(job.status)">{{ statusText(job.status) }}</span>
        </h1>
        <p class="sub">
          {{ job.workflow ? `工作流 · ${job.workflow.title}` : '数字人快捷任务' }}
          <span v-if="job.queue_position"> · 排队第 {{ job.queue_position }} 位</span>
        </p>
      </div>
      <div class="actions">
        <el-button v-if="active" type="warning" plain :disabled="job.status === 'cancelling'" @click="cancel">
          取消任务
        </el-button>
        <el-select v-model="rerunStage" placeholder="续跑全部未完成环节" style="width: 180px" clearable>
          <el-option v-for="s in order" :key="s" :label="`只跑 ${s}`" :value="s" />
        </el-select>
        <el-button type="primary" @click="rerun(false)" :disabled="active">
          <el-icon><RefreshRight /></el-icon><span>{{ rerunStage ? `重跑 ${rerunStage}` : '续跑' }}</span>
        </el-button>
        <el-button type="danger" plain @click="rerun(true)" :disabled="active">全部重跑</el-button>
      </div>
    </div>
    <el-alert v-if="job.error" :title="job.error" type="error" show-icon :closable="false" class="err-alert" />

    <div class="layout">
      <div class="side">
        <div class="panel">
          <div class="panel-head">环节进度 <span class="muted">{{ doneCount }}/{{ order.length }}</span></div>
          <div class="panel-body timeline">
            <div v-for="s in order" :key="s" class="step" :class="stageStatus(s)">
              <div class="step-icon">
                <el-icon v-if="stageStatus(s) === 'success'"><Check /></el-icon>
                <el-icon v-else-if="stageStatus(s) === 'failed'"><Close /></el-icon>
                <el-icon v-else-if="stageStatus(s) === 'running'" class="spin"><Loading /></el-icon>
                <el-icon v-else-if="stageStatus(s) === 'skipped'"><Minus /></el-icon>
              </div>
              <div class="step-main">
                <div class="step-name">{{ s }}</div>
                <div class="step-meta">
                  <span>{{ STAGE_TEXT[stageStatus(s)] || stageStatus(s) }}</span>
                  <span v-if="stageDur(s)"> · {{ stageDur(s) }}</span>
                </div>
                <div v-if="stageError(s)" class="step-err">{{ stageError(s) }}</div>
              </div>
            </div>
          </div>
        </div>
        <div class="panel" v-if="rewriteText">
          <div class="panel-head">{{ job.workflow ? '最终文案' : '文案' }}</div>
          <div class="panel-body rewrite-text">{{ rewriteText }}</div>
        </div>
      </div>

      <div class="primary">
        <div class="panel" v-if="job.final">
          <div class="panel-head">
            成品
            <el-link :href="fileUrl(id, job.final)" type="primary" download :underline="false">
              <el-icon><Download /></el-icon>&nbsp;下载成片
            </el-link>
          </div>
          <div class="panel-body">
            <div class="player"><video :src="fileUrl(id, job.final)" controls class="final-video" /></div>
            <div v-if="job.cover" class="cover">
              <span class="muted">封面</span>
              <el-image :src="fileUrl(id, job.cover)" fit="contain" class="cover-img"
                :preview-src-list="[fileUrl(id, job.cover)]" />
            </div>
          </div>
        </div>
        <div class="panel placeholder" v-else>
          <el-icon :size="40"><VideoCamera /></el-icon>
          <p>{{ active ? '正在生成,成片完成后会显示在这里' : '暂无成片' }}</p>
        </div>

        <div class="panel" v-if="midOutputs.length">
          <div class="panel-head">中间产物 <span class="muted">{{ midOutputs.length }} 个</span></div>
          <div class="panel-body mids">
            <div v-for="m in midOutputs" :key="m.label" class="mid-row">
              <span class="mid-label mono">{{ m.label }}</span>
              <audio v-if="m.kind === 'audio'" :src="m.url" controls class="mid-audio" />
              <video v-else-if="m.kind === 'video'" :src="m.url" controls class="mid-video" />
              <el-image v-else-if="m.kind === 'image'" :src="m.url" fit="contain" style="max-height: 120px" />
              <el-link v-else :href="m.url" target="_blank" type="primary">{{ m.path }}</el-link>
            </div>
          </div>
        </div>
        <div class="panel" v-if="publishResult">
          <div class="panel-head">发布结果</div>
          <div class="panel-body"><pre class="publish-json">{{ publishResult }}</pre></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft, Check, Close, Download, Loading, Minus, RefreshRight, VideoCamera,
} from '@element-plus/icons-vue'
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

const STAGE_TEXT = { success: '完成', failed: '失败', running: '运行中', skipped: '已跳过', pending: '等待中' }
const active = computed(() => isActive(job.value.status))
const order = computed(() => job.value.stage_order || stageNames.value)
const doneCount = computed(() => order.value.filter((s) => ['success', 'skipped'].includes(stageStatus(s))).length)
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
.back { margin: 0 0 6px -10px; color: var(--vh-text-3); }
.back span, .actions .el-button span { margin-left: 4px; }
.mono-title { font-family: var(--vh-mono); font-size: 21px; }
.err-alert { margin-bottom: 20px; border-radius: 10px; }

.layout { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 20px; align-items: start; }
.side, .primary { min-width: 0; }

.timeline { padding: 8px 20px; }
.step { position: relative; display: flex; gap: 14px; padding: 12px 0; }
.step:not(:last-child)::after {
  content: ''; position: absolute; left: 13px; top: 42px; bottom: -6px; width: 2px; background: #eceef4;
}
.step.success:not(:last-child)::after { background: #bbe5c9; }
.step-icon {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0; display: grid; place-items: center;
  background: #f1f2f6; border: 2px solid #e1e3ec; color: #fff; font-size: 14px;
}
.success .step-icon { background: #22c55e; border-color: #22c55e; }
.failed .step-icon { background: #ef4444; border-color: #ef4444; }
.running .step-icon { background: var(--vh-primary); border-color: var(--vh-primary); box-shadow: 0 0 0 4px var(--vh-primary-soft); }
.skipped .step-icon { background: #c4c7d4; border-color: #c4c7d4; }
.spin { animation: rot 1s linear infinite; }
@keyframes rot { to { transform: rotate(360deg); } }
.step-main { min-width: 0; padding-top: 3px; }
.step-name { font-family: var(--vh-mono); font-weight: 600; font-size: 13.5px; }
.pending .step-name { color: var(--vh-text-3); }
.step-meta { font-size: 12px; color: var(--vh-text-3); margin-top: 2px; }
.step-err {
  margin-top: 6px; font-size: 12px; color: #b91c1c; background: #fdecec; border-radius: 6px; padding: 6px 8px;
  word-break: break-all; line-height: 1.5;
}

.rewrite-text { white-space: pre-wrap; line-height: 1.85; font-size: 14px; max-height: 420px; overflow: auto; }

.player { background: #0b0b14; border-radius: 12px; overflow: hidden; display: flex; justify-content: center; }
.final-video { width: 100%; max-height: 600px; display: block; }
.cover { display: flex; align-items: flex-start; gap: 12px; margin-top: 16px; }
.cover-img { max-height: 160px; border-radius: 8px; }
.placeholder { padding: 64px 20px; text-align: center; color: var(--vh-text-3); }
.placeholder p { margin: 10px 0 0; }

.mids { padding-top: 8px; padding-bottom: 8px; }
.mid-row { display: flex; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 1px solid #f2f3f7; }
.mid-row:last-child { border-bottom: none; }
.mid-label { width: 180px; flex-shrink: 0; color: var(--vh-text-2); }
.mid-audio { height: 36px; flex: 1; }
.mid-video { width: 100%; max-height: 300px; background: #000; border-radius: 8px; }
.publish-json {
  white-space: pre-wrap; font-size: 12px; margin: 0; font-family: var(--vh-mono);
  background: #f7f8fb; padding: 12px; border-radius: 8px;
}

@media (max-width: 1080px) {
  .layout { grid-template-columns: 1fr; }
  .mid-row { flex-direction: column; align-items: stretch; gap: 6px; }
  .mid-label { width: auto; }
}
</style>
