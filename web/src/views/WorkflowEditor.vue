<template>
  <div class="editor" v-loading="loading">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <el-input v-model="spec.title" class="title-input" placeholder="工作流名称" />
      <el-tag v-if="builtin" type="info">内置模板(只读,修改后请另存为)</el-tag>
      <el-tag v-else-if="spec.id" type="success">{{ spec.id }}</el-tag>
      <el-tag v-if="dirty" type="warning">未保存</el-tag>
      <span class="spacer" />
      <el-button @click="runValidate">校验</el-button>
      <el-button v-if="!builtin && spec.id" type="primary" :loading="saving" @click="save()">保存</el-button>
      <el-button :loading="saving" @click="saveAs">另存为…</el-button>
      <el-button type="success" @click="goRun">用此工作流创建任务</el-button>
    </div>
    <el-alert v-if="errors.length" type="error" :closable="true" @close="errors = []" class="errors">
      <div v-for="e in errors" :key="e">{{ e }}</div>
    </el-alert>

    <div class="main">
      <!-- 左:步骤面板(拖到画布,或点击添加) -->
      <div class="palette">
        <div v-for="(group, cat) in palette" :key="cat" class="group">
          <div class="group-title">{{ cat }}</div>
          <div v-for="s in group" :key="s.type" class="palette-item" draggable="true" :title="s.description"
            @dragstart="(e) => e.dataTransfer.setData(DND_TYPE, s.type)" @click="addStep(s.type)">
            {{ s.title }}
          </div>
        </div>
        <div class="legend">
          <div v-for="(m, k) in KIND_META" :key="k"><span class="dot" :style="{ background: m.color }" />{{ m.label }}</div>
        </div>
      </div>

      <!-- 中:画布 -->
      <div ref="canvasEl" class="canvas" @dragover.prevent @drop="onDrop">
        <VueFlow :id="FLOW_ID" v-model:nodes="nodes" v-model:edges="edges" :is-valid-connection="isValidConnection"
          :delete-key-code="['Delete', 'Backspace']" :default-edge-options="{ type: 'smoothstep' }"
          :default-viewport="{ x: 40, y: 40, zoom: 1 }" :max-zoom="2" :min-zoom="0.2"
          @nodes-initialized="onNodesInitialized" @connect="onConnect" @node-click="({ node }) => (selectedId = node.id)" @pane-click="selectedId = null"
          @nodes-change="markDirty" @edges-change="markDirty">
          <template #node-step="p">
            <StepNode :id="p.id" :data="p.data" :selected="p.selected" />
          </template>
          <Background :gap="16" />
          <Controls />
          <MiniMap pannable zoomable />
        </VueFlow>
        <div v-if="!nodes.length" class="empty-hint">从左侧把步骤拖到这里,再从输出端口(右侧圆点)拖线到下游的输入端口</div>
      </div>

      <!-- 右:属性面板 -->
      <div class="side-panel">
        <template v-if="selected">
          <div class="panel-title">{{ selected.data.meta?.title }}</div>
          <div class="panel-desc">{{ selected.data.meta?.description }}</div>
          <el-form label-position="top" size="small">
            <el-form-item label="节点 ID(任务目录名 / CLI --set 用)">
              <el-input :model-value="selected.id" disabled />
            </el-form-item>
            <el-form-item label="显示名称">
              <el-input v-model="selected.data.title" :placeholder="selected.data.meta?.title" @input="markDirty" />
            </el-form-item>
            <ParamField v-for="p in selected.data.meta?.params || []" :key="p.key" :param="p" :assets="assets"
              :model-value="selected.data.params[p.key]" @update:model-value="(v) => setParam(p.key, v)" />
          </el-form>
          <el-button type="danger" plain size="small" @click="removeSelected">删除节点</el-button>
        </template>
        <template v-else>
          <div class="panel-title">工作流设置</div>
          <el-form label-position="top" size="small">
            <el-form-item label="描述">
              <el-input v-model="spec.description" type="textarea" :rows="3" @input="markDirty" />
            </el-form-item>
            <el-form-item label="成品输出(任务列表/详情页展示的视频)">
              <el-select v-model="spec.final" clearable placeholder="自动:最后一个视频输出" @change="markDirty">
                <el-option v-for="o in videoOutputs" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </el-form-item>
          </el-form>
          <div class="tips">
            <p>· 拖动左侧步骤到画布添加节点;点击节点编辑参数。</p>
            <p>· 从节点右侧输出端口拖线到下游左侧输入端口;颜色表示数据类型,类型不符的连线会被拒绝。</p>
            <p>· 带 <b style="color: #f56c6c">*</b> 的输入必须连线;选中节点或连线后按 Delete 删除。</p>
            <p>· 输入类节点(文案/视频/音频)的参数在创建任务时填写。</p>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import StepNode from '../components/StepNode.vue'
import ParamField from '../components/ParamField.vue'
import {
  KIND_META, getAssets, getWorkflow, kindsCompatible, listSteps, saveWorkflow, validateWorkflow,
} from '../api.js'

const props = defineProps({ id: String })
const router = useRouter()
const FLOW_ID = 'vh-workflow-editor'
const DND_TYPE = 'application/vh-step'
const ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/

const { screenToFlowCoordinate, removeNodes, findNode, fitView } = useVueFlow(FLOW_ID)
const canvasEl = ref(null)

const loading = ref(true)
const saving = ref(false)
const dirty = ref(false)
const builtin = ref(false)
const errors = ref([])
const steps = ref({})
const assets = ref({})
const spec = reactive({ id: '', title: '未命名工作流', description: '', final: null })
const nodes = ref([])
const edges = ref([])
const selectedId = ref(null)
let loaded = false

const selected = computed(() => nodes.value.find((n) => n.id === selectedId.value) || null)
const palette = computed(() => {
  const groups = {}
  for (const s of Object.values(steps.value)) (groups[s.category] ||= []).push(s)
  return groups
})
const videoOutputs = computed(() => nodes.value.flatMap((n) => (n.data.meta?.outputs || [])
  .filter((o) => o.kind === 'video')
  .map((o) => ({ value: `${n.id}.${o.name}`, label: `${n.data.title || n.data.meta.title} · ${o.title}` }))))

// 打开已有工作流时(节点尺寸测量完成后)适配视口一次;缩放上限 1,避免节点少时被放得过大
let fitted = false
function onNodesInitialized() {
  if (fitted || !nodes.value.length) return
  fitted = true
  fitView({ padding: 0.1, maxZoom: 1 })
}

function markDirty() {
  if (loaded) dirty.value = true
}

// ------------------------------------------------------------ 数据转换

function edgeStyle(sourceId, sourceHandle) {
  const n = nodes.value.find((x) => x.id === sourceId)
  const kind = n?.data.meta?.outputs.find((o) => o.name === sourceHandle)?.kind
  return { stroke: KIND_META[kind]?.color || '#b1b3b8', strokeWidth: 2 }
}

function toFlowEdge(e) {
  return {
    id: `${e.source}.${e.sourceHandle}->${e.target}.${e.targetHandle}`,
    source: e.source, sourceHandle: e.sourceHandle, target: e.target, targetHandle: e.targetHandle,
    type: 'smoothstep', style: edgeStyle(e.source, e.sourceHandle),
  }
}

function makeNode(id, stepType, position, params = {}, title = '') {
  return { id, type: 'step', position, data: { stepType, title, params: { ...params }, meta: steps.value[stepType] } }
}

function toSpec() {
  const cleanParams = (p) => Object.fromEntries(Object.entries(p).filter(([, v]) => v !== undefined && v !== null))
  return {
    id: spec.id,
    title: spec.title,
    description: spec.description,
    final: spec.final || null,
    nodes: nodes.value.map((n) => ({
      id: n.id,
      type: n.data.stepType,
      ...(n.data.title ? { title: n.data.title } : {}),
      params: cleanParams(n.data.params),
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.value.map((e) => ({
      source: e.source, sourceHandle: e.sourceHandle, target: e.target, targetHandle: e.targetHandle,
    })),
  }
}

// ------------------------------------------------------------ 编辑操作

function newNodeId(stepType) {
  const taken = new Set(nodes.value.map((n) => n.id))
  for (let i = 1; ; i++) {
    const id = `${stepType}_${i}`
    if (!taken.has(id)) return id
  }
}

function addStep(stepType, position) {
  // 点击添加:放在当前视口中部,依次错开,避免叠在一起
  const rect = canvasEl.value.getBoundingClientRect()
  const offset = (nodes.value.length % 6) * 24
  const pos = position || screenToFlowCoordinate({ x: rect.left + rect.width / 2 - 95 + offset, y: rect.top + rect.height / 3 + offset })
  const node = makeNode(newNodeId(stepType), stepType, pos)
  nodes.value = [...nodes.value, node]
  selectedId.value = node.id
  markDirty()
}

function onDrop(e) {
  const stepType = e.dataTransfer?.getData(DND_TYPE)
  if (!stepType) return
  addStep(stepType, screenToFlowCoordinate({ x: e.clientX - 95, y: e.clientY - 20 }))
}

function portKind(nodeId, handle, dir) {
  const n = findNode(nodeId) || nodes.value.find((x) => x.id === nodeId)
  return n?.data.meta?.[dir].find((p) => p.name === handle)?.kind
}

// 连线规则与后端校验一致:类型兼容、每个输入只能连一条、不能自连
function isValidConnection(c) {
  if (c.source === c.target) return false
  const src = portKind(c.source, c.sourceHandle, 'outputs')
  const dst = portKind(c.target, c.targetHandle, 'inputs')
  if (!src || !dst || !kindsCompatible(src, dst)) return false
  // Vue Flow 也会用它复检已有连线,所以"已占用"要排除连线自身
  return !edges.value.some((e) => e.target === c.target && e.targetHandle === c.targetHandle
    && !(e.source === c.source && e.sourceHandle === c.sourceHandle))
}

function onConnect(c) {
  if (!isValidConnection(c)) return
  edges.value = [...edges.value, toFlowEdge(c)]
  markDirty()
}

function setParam(key, value) {
  const n = selected.value
  if (!n) return
  n.data.params = { ...n.data.params, [key]: value }
  markDirty()
}

function removeSelected() {
  if (!selected.value) return
  removeNodes([selected.value.id], true)
  selectedId.value = null
  markDirty()
}

// ------------------------------------------------------------ 校验 / 保存 / 运行

async function runValidate() {
  try {
    const r = await validateWorkflow({ ...toSpec(), id: spec.id || 'draft' })
    errors.value = r.errors
    if (r.ok) ElMessage.success('校验通过')
    return r.ok
  } catch (e) {
    ElMessage.error(e.message)
    return false
  }
}

async function save(id = spec.id) {
  saving.value = true
  try {
    await saveWorkflow({ ...toSpec(), id })
    errors.value = []
    dirty.value = false
    ElMessage.success('已保存')
    if (id !== props.id) {
      builtin.value = false
      spec.id = id
      router.replace(`/workflows/${id}/edit`)
    }
    return true
  } catch (e) {
    errors.value = e.message.split(';')
    return false
  } finally {
    saving.value = false
  }
}

async function saveAs() {
  try {
    const { value } = await ElMessageBox.prompt('新工作流 ID(小写字母、数字、下划线、连字符)', '另存为', {
      inputValue: spec.id ? `${spec.id}_copy` : '',
      inputPattern: ID_PATTERN,
      inputErrorMessage: 'ID 格式不正确',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    await save(value)
  } catch {
    // 取消
  }
}

async function goRun() {
  if (!spec.id || builtin.value) {
    if (builtin.value && !dirty.value) return router.push(`/workflows/${spec.id}/run`)
    ElMessage.warning('请先另存为自己的工作流')
    return saveAs()
  }
  if (dirty.value && !(await save())) return
  router.push(`/workflows/${spec.id}/run`)
}

onMounted(async () => {
  try {
    const [stepList, assetList] = await Promise.all([listSteps(), getAssets()])
    steps.value = Object.fromEntries(stepList.map((s) => [s.type, s]))
    assets.value = assetList
    if (props.id && props.id !== 'new') {
      const w = await getWorkflow(props.id)
      Object.assign(spec, { id: w.id, title: w.title, description: w.description, final: w.final })
      builtin.value = w.builtin
      nodes.value = w.nodes.map((n, i) => makeNode(n.id, n.type, n.position || { x: i * 260, y: 0 }, n.params, n.title))
      edges.value = w.edges.map(toFlowEdge)
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
    // 初次加载触发的 nodes-change 不算修改
    setTimeout(() => { loaded = true }, 300)
  }
})
</script>

<style scoped>
.editor { display: flex; flex-direction: column; height: calc(100vh - 88px); }
.toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; padding: 10px 12px;
  background: #fff; border: 1px solid var(--vh-border); border-radius: 12px; box-shadow: var(--vh-shadow);
}
.title-input { width: 260px; }
.title-input :deep(.el-input__wrapper) { box-shadow: none; font-weight: 600; font-size: 15px; }
.title-input :deep(.el-input__wrapper:hover), .title-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
}
.spacer { flex: 1; }
.errors { margin-bottom: 12px; border-radius: 10px; }
.main {
  flex: 1; display: flex; min-height: 0; border: 1px solid var(--vh-border); background: #fff;
  border-radius: 14px; overflow: hidden; box-shadow: var(--vh-shadow);
}
.palette { width: 168px; border-right: 1px solid var(--vh-border); overflow-y: auto; padding: 12px 10px; background: #fafbfd; }
.group-title { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; color: var(--vh-text-3); margin: 10px 4px 6px; }
.palette-item {
  padding: 8px 10px; margin-bottom: 6px; border: 1px solid var(--vh-border); border-radius: 8px;
  font-size: 13px; cursor: grab; background: #fff; user-select: none; transition: all 0.15s;
}
.palette-item:hover { border-color: var(--vh-primary); color: var(--vh-primary); box-shadow: 0 2px 8px rgba(91, 91, 240, 0.12); }
.legend { margin: 16px 4px 0; font-size: 12px; color: var(--vh-text-2); line-height: 1.9; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
.canvas { flex: 1; position: relative; min-width: 0; background: #f8f9fc; }
.empty-hint {
  position: absolute; top: 45%; left: 0; right: 0; text-align: center; color: var(--vh-text-3);
  pointer-events: none; font-size: 14px; padding: 0 24px;
}
.side-panel { width: 310px; border-left: 1px solid var(--vh-border); overflow-y: auto; padding: 16px; }
.panel-title { font-weight: 700; font-size: 15px; margin-bottom: 4px; }
.panel-desc { color: var(--vh-text-3); font-size: 12px; margin-bottom: 14px; line-height: 1.6; }
.tips { color: var(--vh-text-3); font-size: 12px; line-height: 1.7; background: #f7f8fb; border-radius: 10px; padding: 8px 12px; }
.tips p { margin: 4px 0; }
</style>
