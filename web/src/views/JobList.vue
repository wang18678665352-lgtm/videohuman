<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>任务中心</h1>
        <p v-if="commercialMode" class="sub">商用模式：成片必带 AI 生成角标，授权记录可导出。</p>
        <p v-else class="sub">查看所有生成任务的进度与成片,失败的任务可以从中断处续跑。</p>
      </div>
      <div class="actions">
        <el-button @click="$router.push('/workflows')"><el-icon><Share /></el-icon><span>从工作流创建</span></el-button>
        <el-button type="primary" @click="$router.push('/new')"><el-icon><Plus /></el-icon><span>新建任务</span></el-button>
      </div>
    </div>

    <el-alert
      v-if="failedRequired.length"
      type="warning"
      show-icon
      :closable="false"
      class="ready-banner"
      title="还不能出片"
    >
      <div v-for="action in failedActions" :key="action">{{ action }}</div>
    </el-alert>
    <p v-else-if="readiness?.ok" class="ready-ok">
      <span class="status-pill success">机器已就绪，可以出片</span>
    </p>

    <div class="stats">
      <div v-for="c in statCards" :key="c.label" class="stat panel">
        <div class="stat-icon" :style="{ background: c.bg, color: c.color }"><el-icon :size="18"><component :is="c.icon" /></el-icon></div>
        <div>
          <div class="stat-num">{{ c.value }}</div>
          <div class="stat-label">{{ c.label }}</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span>全部任务 <span class="muted">· {{ jobs.length }}</span></span>
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="active">进行中</el-radio-button>
          <el-radio-button value="done">已完成</el-radio-button>
          <el-radio-button value="failed">失败</el-radio-button>
        </el-radio-group>
      </div>
      <el-table :data="shownJobs" v-loading="loading" @row-click="(r) => $router.push(`/jobs/${r.job_id}`)"
        row-class-name="job-row">
        <template #empty>
          <div class="empty">
            <el-icon :size="40"><VideoCamera /></el-icon>
            <p>{{ jobs.length ? '没有符合筛选条件的任务' : '还没有任务,先创建一个吧' }}</p>
            <div v-if="!jobs.length" class="empty-actions" @click.stop>
              <el-button type="primary" @click="$router.push('/workflows/digital_human/run')">数字人口播</el-button>
              <el-button type="primary" @click="$router.push('/workflows/broll_voiceover/run')">配音混剪</el-button>
              <el-button type="primary" @click="$router.push('/workflows/video_translate/run')">视频翻译</el-button>
              <el-button text @click="$router.push('/new')">数字人快捷任务</el-button>
            </div>
          </div>
        </template>
        <el-table-column label="任务" min-width="220">
          <template #default="{ row }">
            <div class="job-cell">
              <div class="job-thumb" :class="{ ready: row.has_final }">
                <el-icon :size="16"><VideoPlay v-if="row.has_final" /><VideoCamera v-else /></el-icon>
              </div>
              <div class="job-meta">
                <div class="job-id">{{ row.job_id }}</div>
                <div class="job-mode">{{ modeText(row) }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="status-pill" :class="statusType(row.status)">{{ statusText(row.status) }}</span>
            <div v-if="row.queue_position" class="muted q">排队第 {{ row.queue_position }} 位</div>
          </template>
        </el-table-column>
        <el-table-column label="环节进度" min-width="240">
          <template #default="{ row }">
            <div class="progress">
              <div class="stage-bar">
                <el-tooltip v-for="s in row.stage_order || stageNames" :key="s" :content="`${s}: ${row.stages[s] || 'pending'}`"
                  placement="top">
                  <span class="seg" :class="dotClass(row.stages[s])" />
                </el-tooltip>
              </div>
              <span class="progress-text">{{ progressText(row) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }"><span class="muted">{{ fmtTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right" align="right">
          <template #default="{ row }">
            <div class="ops" @click.stop>
              <el-button v-if="isActive(row.status)" size="small" type="warning" plain
                :disabled="row.status === 'cancelling'" @click="cancel(row)">取消</el-button>
              <el-button v-else size="small" @click="rerun(row)">续跑</el-button>
              <el-button size="small" type="success" plain @click="openPublish(row)" :disabled="!canPublish(row)">发布</el-button>
              <el-dropdown trigger="click" @command="(c) => c === 'detail' ? $router.push(`/jobs/${row.job_id}`) : remove(row)">
                <el-button size="small" text><el-icon><MoreFilled /></el-icon></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="detail">查看详情</el-dropdown-item>
                    <el-dropdown-item command="delete" :disabled="isActive(row.status)" divided>
                      <span class="danger-text">删除任务</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="publishDlg" title="发布到平台" width="480px">
      <el-form label-width="80px">
        <el-form-item label="平台">
          <el-checkbox-group v-model="publishForm.platforms">
            <el-checkbox value="douyin">抖音</el-checkbox>
            <el-checkbox value="bilibili">B站</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="publishForm.title" placeholder="留空取文案首句" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="publishForm.tags" placeholder="逗号分隔,可留空" />
        </el-form-item>
      </el-form>
      <p class="hint publish-note">抖音发布遇到短信或滑块时需要你在弹出的浏览器里完成验证，验证完成前不算发布成功。</p>
      <template #footer>
        <el-button @click="publishDlg = false">取消</el-button>
        <el-button type="primary" :loading="publishing" @click="doPublish">开始发布</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CircleCheck, CircleClose, Loading, MoreFilled, Plus, Share, VideoCamera, VideoPlay,
} from '@element-plus/icons-vue'
import {
  STATUS_TEXT, STATUS_TYPE, cancelJob, deleteJob, getReadiness, getSettings, getUsage, isActive, listJobs, runJob,
} from '../api.js'

const jobs = ref([])
const loading = ref(false)
const commercialMode = ref(false)
const readiness = ref(null)
const usage = ref(null)
const failedActions = computed(() => [...new Set(failedRequired.value.map((c) => c.action))])
const failedRequired = computed(() =>
  (readiness.value?.checks || []).filter((c) => c.required && !c.ok),
)
const stageNames = ref(['download', 'transcribe', 'rewrite', 'tts', 'avatar', 'postprocess', 'publish'])
const filter = ref('all')
let timer = null

const shownJobs = computed(() => jobs.value.filter((j) => {
  if (filter.value === 'active') return isActive(j.status)
  if (filter.value === 'done') return j.status === 'done'
  if (filter.value === 'failed') return ['failed', 'interrupted', 'cancelled'].includes(j.status)
  return true
}))
const statCards = computed(() => {
  const count = (fn) => jobs.value.filter(fn).length
  return [
    { label: '全部任务', value: jobs.value.length, icon: VideoCamera, color: '#5b5bf0', bg: '#eef0ff' },
    { label: '进行中', value: count((j) => isActive(j.status)), icon: Loading, color: '#0ea5e9', bg: '#e6f6fd' },
    { label: '已完成', value: count((j) => j.status === 'done'), icon: CircleCheck, color: '#16a34a', bg: '#e8f6ed' },
    { label: '失败 / 中断', value: count((j) => ['failed', 'interrupted'].includes(j.status)), icon: CircleClose, color: '#ef4444', bg: '#fdecec' },
  ]
})

const publishDlg = ref(false)
const publishing = ref(false)
const publishJob = ref(null)
const publishForm = ref({ platforms: ['douyin'], title: '', tags: '' })

function statusType(s) {
  return STATUS_TYPE[s] || 'info'
}
function statusText(s) {
  return STATUS_TEXT[s] || s
}
function dotClass(s) {
  return { success: 'ok', skipped: 'skip', failed: 'bad', running: 'run' }[s] || 'pending'
}
function modeText(row) {
  if (row.workflow) return `工作流 · ${row.workflow.title}`
  return { clone: '抖音克隆', audio: '音频驱动', text: '文案直给' }[row.mode] || '—'
}
function progressText(row) {
  const order = row.stage_order || stageNames.value
  const done = order.filter((s) => ['success', 'skipped'].includes(row.stages[s])).length
  return `${done}/${order.length}`
}
function fmtTime(t) {
  return t ? new Date(t).toLocaleString('zh-CN') : '-'
}

async function refresh() {
  let active = false
  try {
    jobs.value = await listJobs()
    active = jobs.value.some((j) => isActive(j.status))
  } catch (e) {
    ElMessage.error(`刷新任务列表失败: ${e.message}`)
  }
  clearTimeout(timer)
  timer = setTimeout(refresh, active ? 2000 : 10000)
}

async function act(fn, okMsg) {
  try {
    await fn()
    ElMessage.success(okMsg)
  } catch (e) {
    ElMessage.error(e.message)
  }
  refresh()
}

const rerun = (row) => act(() => runJob(row.job_id, { resume: true }), `已入队续跑 ${row.job_id}`)
const cancel = (row) => act(() => cancelJob(row.job_id), `已请求取消 ${row.job_id}`)

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除任务 ${row.job_id} 及其全部产物?此操作不可恢复。`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  act(() => deleteJob(row.job_id), `已删除 ${row.job_id}`)
}

// 工作流任务需包含 publish 节点才能发布
const canPublish = (row) => row.has_final && !isActive(row.status) && (row.stage_order || []).includes('publish')

function openPublish(row) {
  publishJob.value = row
  publishForm.value = { platforms: ['douyin'], title: '', tags: '' }
  publishDlg.value = true
}

async function doPublish() {
  if (!publishForm.value.platforms.length) return ElMessage.warning('至少选一个平台')
  publishing.value = true
  try {
    const params = { publish_platforms: publishForm.value.platforms }
    if (publishForm.value.title) params.publish_title = publishForm.value.title
    if (publishForm.value.tags) {
      params.publish_tags = publishForm.value.tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
    }
    // 工作流任务的参数按节点下发
    const body = publishJob.value.workflow ? { node_params: { publish: params } } : params
    await runJob(publishJob.value.job_id, { only: 'publish', resume: true, params: body })
    ElMessage.success('发布任务已入队,抖音发布若弹验证请在浏览器中完成')
    publishDlg.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    publishing.value = false
  }
}

onMounted(async () => {
  loading.value = true
  const readyP = getReadiness().then((r) => {
    readiness.value = r
    if (r?.commercial_mode) commercialMode.value = true
  }).catch(() => {
    readiness.value = null
  })
  const usageP = getUsage().then((u) => {
    usage.value = u
    if (u?.commercial_mode) commercialMode.value = true
  }).catch(() => {
    usage.value = null
  })
  try {
    const s = await getSettings()
    stageNames.value = s.stage_names
    if (s.commercial_mode) commercialMode.value = true
    await refresh()
  } finally {
    loading.value = false
    await Promise.all([readyP, usageP])
  }
})
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.page-head .el-button span { margin-left: 6px; }

.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 20px; }
.stat { display: flex; align-items: center; gap: 14px; padding: 18px 20px; }
.stat + .stat { margin-top: 0; }
.stat-icon { width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center; flex-shrink: 0; }
.stat-num { font-size: 24px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.stat-label { color: var(--vh-text-3); font-size: 13px; margin-top: 2px; }

.panel :deep(.job-row) { cursor: pointer; }
.panel :deep(.el-table) { border-radius: 0 0 var(--vh-radius) var(--vh-radius); }
.panel :deep(.el-table__inner-wrapper::before) { display: none; }

.job-cell { display: flex; align-items: center; gap: 12px; }
.job-thumb {
  width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; flex-shrink: 0;
  background: #f1f2f6; color: var(--vh-text-3);
}
.job-thumb.ready { background: linear-gradient(135deg, #6366f1, #a855f7); color: #fff; }
.job-meta { min-width: 0; }
.job-id { font-weight: 600; color: var(--vh-text); font-family: var(--vh-mono); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-mode { color: var(--vh-text-3); font-size: 12px; margin-top: 2px; }
.q { font-size: 12px; margin-top: 4px; }

.progress { display: flex; align-items: center; gap: 10px; }
.stage-bar { display: flex; gap: 3px; flex: 1; max-width: 220px; }
.seg { flex: 1; height: 6px; border-radius: 3px; background: #e7e8ef; }
.seg.ok { background: #22c55e; }
.seg.skip { background: #c4c7d4; }
.seg.bad { background: #ef4444; }
.seg.run { background: var(--vh-primary); animation: vh-pulse 1s ease-in-out infinite; }
.progress-text { font-size: 12px; color: var(--vh-text-3); font-variant-numeric: tabular-nums; width: 32px; }

.ops { display: inline-flex; align-items: center; gap: 4px; }
.ops .el-button + .el-button { margin-left: 4px; }
.danger-text { color: var(--el-color-danger); }

.empty { padding: 40px 0; color: var(--vh-text-3); display: flex; flex-direction: column; align-items: center; }
.empty p { margin: 10px 0 16px; }
.empty-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; align-items: center; max-width: 520px; }
.empty-actions :deep(.el-button) { margin-left: 0; }
.ready-banner { margin-bottom: 16px; }
.ready-banner :deep(.el-alert__title) { white-space: pre-line; }
.ready-ok { margin: 0 0 16px; }
.publish-note { margin: 0; }

@media (max-width: 960px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .panel-head { flex-wrap: wrap; }
}
</style>
