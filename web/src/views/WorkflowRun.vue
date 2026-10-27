<template>
  <div style="max-width: 820px" v-loading="loading">
    <h2>{{ flow.title || id }}</h2>
    <p class="desc">{{ flow.description }}</p>
    <el-form label-width="120px">
      <el-form-item label="任务 ID">
        <el-input v-model="jobId" placeholder="留空自动生成" style="width: 320px" />
      </el-form-item>

      <!-- 输入类节点:本次任务的素材与文案 -->
      <template v-for="n in inputNodes" :key="n.id">
        <el-divider content-position="left">{{ n.title || stepOf(n).title }}</el-divider>
        <ParamField v-for="p in stepOf(n).params" :key="p.key" :param="p" :assets="assets" :text-rows="6"
          v-model="values[n.id][p.key]" />
        <div v-if="previewUrl(n)" class="preview">
          <video v-if="n.type === 'input_video'" :src="previewUrl(n)" controls class="preview-video" />
          <audio v-else :src="previewUrl(n)" controls />
        </div>
      </template>

      <el-divider content-position="left">各步骤参数(可选,默认值来自工作流)</el-divider>
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

      <el-form-item style="margin-top: 20px">
        <el-button type="primary" :loading="submitting" @click="submit">创建并执行</el-button>
        <el-button @click="$router.push(`/workflows/${id}/edit`)">查看流程图</el-button>
        <el-button @click="$router.push('/workflows')">返回</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
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
.desc { color: #606266; font-size: 13px; margin-top: -8px; }
.node-title { font-weight: 600; margin-right: 8px; }
.node-type { color: #909399; font-size: 12px; font-family: monospace; }
.hint { color: #909399; font-size: 12px; }
.preview { margin: -8px 0 16px 120px; }
.preview-video { max-width: 320px; max-height: 240px; background: #000; }
</style>
