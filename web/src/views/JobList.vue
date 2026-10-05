<template>
  <div>
    <div class="bar">
      <h2>任务列表</h2>
      <el-button type="primary" @click="$router.push('/new')">新建任务</el-button>
    </div>
    <el-table :data="jobs" v-loading="loading" @row-click="(r) => $router.push(`/jobs/${r.job_id}`)" row-style="cursor:pointer">
      <el-table-column prop="job_id" label="任务 ID" min-width="160" />
      <el-table-column label="模式" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.mode === 'clone'" type="warning" size="small">克隆</el-tag>
          <el-tag v-else-if="row.mode === 'audio'" type="success" size="small">音频</el-tag>
          <el-tag v-else-if="row.mode === 'text'" size="small">文案</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="环节进度" min-width="220">
        <template #default="{ row }">
          <div class="stage-dots">
            <el-tooltip v-for="s in stageNames" :key="s" :content="`${s}: ${row.stages[s] || 'pending'}`">
              <span class="dot" :class="dotClass(row.stages[s])" />
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="成品" width="70">
        <template #default="{ row }">
          <el-icon v-if="row.has_final" color="#67c23a"><VideoPlay /></el-icon>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click.stop="$router.push(`/jobs/${row.job_id}`)">详情</el-button>
          <el-button size="small" @click.stop="rerun(row)" :disabled="row.status === 'running' || row.status === 'queued'">重跑</el-button>
          <el-button size="small" type="success" @click.stop="openPublish(row)" :disabled="!row.has_final">发布</el-button>
        </template>
      </el-table-column>
    </el-table>

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
      <template #footer>
        <el-button @click="publishDlg = false">取消</el-button>
        <el-button type="primary" :loading="publishing" @click="doPublish">开始发布</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPlay } from '@element-plus/icons-vue'
import { listJobs, runJob, getSettings } from '../api.js'

const jobs = ref([])
const loading = ref(false)
const stageNames = ref(['download', 'transcribe', 'rewrite', 'tts', 'avatar', 'postprocess', 'publish'])
let timer = null

const publishDlg = ref(false)
const publishing = ref(false)
const publishJob = ref(null)
const publishForm = ref({ platforms: ['douyin'], title: '', tags: '' })

function statusType(s) {
  return { done: 'success', failed: 'danger', running: 'primary', queued: 'info' }[s] || 'info'
}
function statusText(s) {
  return { done: '完成', failed: '失败', running: '运行中', queued: '排队中', interrupted: '中断' }[s] || s
}
function dotClass(s) {
  return { success: 'ok', skipped: 'skip', failed: 'bad', running: 'run' }[s] || 'pending'
}
function fmtTime(t) {
  return t ? new Date(t).toLocaleString('zh-CN') : '-'
}

async function refresh() {
  jobs.value = await listJobs()
  const active = jobs.value.some((j) => j.status === 'running' || j.status === 'queued')
  clearTimeout(timer)
  timer = setTimeout(refresh, active ? 2000 : 10000)
}

async function rerun(row) {
  await runJob(row.job_id, { resume: true })
  ElMessage.success(`已入队续跑 ${row.job_id}`)
  refresh()
}

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
    await runJob(publishJob.value.job_id, { only: 'publish', resume: true, params })
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
  try {
    const s = await getSettings()
    stageNames.value = s.stage_names
    await refresh()
  } finally {
    loading.value = false
  }
})
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.bar h2 { margin: 0; }
.stage-dots { display: flex; gap: 6px; }
.dot { width: 14px; height: 14px; border-radius: 3px; background: #dcdfe6; display: inline-block; }
.dot.ok { background: #67c23a; }
.dot.skip { background: #b1b3b8; }
.dot.bad { background: #f56c6c; }
.dot.run { background: #409eff; animation: blink 1s infinite alternate; }
@keyframes blink { from { opacity: 1; } to { opacity: 0.3; } }
</style>
