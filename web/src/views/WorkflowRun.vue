<template>
  <div class="page narrow" v-loading="loading">
    <div class="page-head">
      <div>
        <el-button text class="back" @click="$router.push('/workflows')"><el-icon><ArrowLeft /></el-icon><span>工作流</span></el-button>
        <h1>{{ flow.title || id }}</h1>
        <p class="sub">{{ flow.description }}</p>
      </div>
      <div class="actions">
        <el-button @click="$router.push(`/workflows/${id}/edit`)"><el-icon><Share /></el-icon><span>查看流程图</span></el-button>
      </div>
    </div>
    <el-form label-width="120px" label-position="left">
      <!-- 输入类节点:本次任务的素材与文案 -->
      <section v-for="(n, i) in inputNodes" :key="n.id" class="panel">
        <div class="panel-head"><span><b class="num">{{ i + 1 }}</b>{{ n.title || stepOf(n).title }}</span></div>
        <div class="panel-body">
          <ParamField v-for="p in stepOf(n).params" :key="p.key" :param="p" :assets="assets" :text-rows="6"
            v-model="values[n.id][p.key]" />
          <div v-if="previewUrl(n)" class="preview">
            <video v-if="n.type === 'input_video'" :src="previewUrl(n)" controls class="preview-video" />
            <audio v-else :src="previewUrl(n)" controls />
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span><b class="num"><el-icon><Setting /></el-icon></b>各步骤参数</span>
          <span class="muted">可选,默认值来自工作流</span>
        </div>
        <div class="panel-body steps">
          <el-collapse>
            <el-collapse-item v-for="n in otherNodes" :key="n.id" :name="n.id">
              <template #title>
                <span class="node-title">{{ n.title || stepOf(n).title }}</span>
                <span class="node-type">{{ stepOf(n).title }} · {{ n.id }}</span>
              </template>
              <div v-if="!stepOf(n).params.length" class="hint">该步骤没有可调参数</div>
              <ParamField v-for="p in stepOf(n).params" :key="p.key" :param="p" :assets="assets"
                v-model="values[n.id][p.key]" />
            </el-collapse-item>
          </el-collapse>
        </div>
      </section>

      <section class="panel">
        <div class="panel-body">
          <el-form-item label="任务 ID" style="margin-bottom: 0">
            <el-input v-model="jobId" placeholder="留空自动生成" />
          </el-form-item>
        </div>
      </section>

      <div class="submit-bar">
        <el-button size="large" @click="$router.push('/workflows')">返回</el-button>
        <el-button type="primary" size="large" :loading="submitting" @click="submit">
          <el-icon><VideoPlay /></el-icon><span>创建并执行</span>
        </el-button>
      </div>
    </el-form>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Setting, Share, VideoPlay } from '@element-plus/icons-vue'
import ParamField from '../components/ParamField.vue'
import { assetUrl, createJob, getAssets, getWorkflow, listSteps } from '../api.js'

const props = defineProps({ id: String })
const router = useRouter()
const loading = ref(true)
const submitting = ref(false)
const flow = ref({ nodes: [] })
const steps = ref({})
const assets = ref({})
const jobId = ref('')
const values = reactive({}) // { nodeId: { key: value } }

const stepOf = (n) => steps.value[n.type] || { title: n.type, params: [], category: '' }
const inputNodes = computed(() => flow.value.nodes.filter((n) => stepOf(n).category === '输入'))
const otherNodes = computed(() => flow.value.nodes.filter((n) => stepOf(n).category !== '输入'))

function previewUrl(n) {
  const path = values[n.id]?.path
  return path && (n.type === 'input_video' || n.type === 'input_audio') ? assetUrl(path) : ''
}

// 只提交与工作流默认值不同的参数;输入类节点全部提交
function nodeParams() {
  const out = {}
  for (const n of flow.value.nodes) {
    const isInput = stepOf(n).category === '输入'
    const changed = Object.entries(values[n.id] || {}).filter(([k, v]) => v !== undefined && v !== null
      && (isInput || v !== n.params?.[k]))
    if (changed.length) out[n.id] = Object.fromEntries(changed)
  }
  return out
}

async function submit() {
  for (const n of flow.value.nodes) {
    for (const p of stepOf(n).params) {
      const v = values[n.id][p.key]
      if (p.required && (v === undefined || v === null || String(v).trim() === '')) {
        return ElMessage.warning(`请填写「${n.title || stepOf(n).title}」的${p.title}`)
      }
    }
  }
  submitting.value = true
  try {
    const body = { workflow: props.id, node_params: nodeParams() }
    if (jobId.value.trim()) body.job_id = jobId.value.trim()
    const r = await createJob(body)
    ElMessage.success(`任务 ${r.job_id} 已创建`)
    router.push(`/jobs/${r.job_id}`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    const [w, stepList, assetList] = await Promise.all([getWorkflow(props.id), listSteps(), getAssets()])
    steps.value = Object.fromEntries(stepList.map((s) => [s.type, s]))
    assets.value = assetList
    for (const n of w.nodes) values[n.id] = { ...(n.params || {}) }
    flow.value = w
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.back { margin: 0 0 6px -10px; color: var(--vh-text-3); }
.back span, .actions .el-button span, .submit-bar .el-button span { margin-left: 4px; }
section.panel { margin-bottom: 16px; }
section.panel + section.panel { margin-top: 0; }
.num {
  display: inline-grid; place-items: center; width: 22px; height: 22px; margin-right: 10px; border-radius: 7px;
  background: var(--vh-primary-soft); color: var(--vh-primary); font-size: 12px; vertical-align: -1px;
}
.panel-body :deep(.el-form-item:last-child) { margin-bottom: 0; }
.panel-body :deep(.el-select) { width: 100%; }
.steps { padding-top: 4px; padding-bottom: 4px; }
.node-title { font-weight: 600; margin-right: 10px; }
.node-type { color: var(--vh-text-3); font-size: 12px; font-family: var(--vh-mono); }
.preview { margin: 4px 0 0 120px; }
.preview-video { max-width: 320px; max-height: 240px; background: #000; border-radius: 10px; }
.submit-bar {
  position: sticky; bottom: 0; z-index: 5; display: flex; justify-content: flex-end; gap: 10px;
  padding: 16px 0; background: linear-gradient(to top, var(--vh-bg) 70%, transparent);
}
@media (max-width: 768px) { .preview { margin-left: 0; } }
</style>
