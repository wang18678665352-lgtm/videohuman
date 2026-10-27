<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>工作流</h1>
        <p class="sub">
          把「输入 → 文本 → 音频 → 视频 → 后期 → 发布」各步骤自由连起来。内置模板可直接使用,
          也可以打开编辑器复制一份,按需增删节点、改参数。
        </p>
      </div>
      <div class="actions">
        <el-button type="primary" @click="$router.push('/workflows/new/edit')">
          <el-icon><Plus /></el-icon><span>新建空白工作流</span>
        </el-button>
      </div>
    </div>

    <div class="grid" v-loading="loading">
      <div v-for="(w, i) in flows" :key="w.id" class="card panel" :class="{ broken: w.error }">
        <div class="card-top">
          <div class="icon" :style="{ background: gradient(i) }"><el-icon :size="20"><Share /></el-icon></div>
          <span class="badge" :class="w.builtin ? 'builtin' : 'custom'">{{ w.builtin ? '内置模板' : '自定义' }}</span>
        </div>
        <div class="title">{{ w.title }}</div>
        <div class="desc">{{ w.error ? `文件损坏:${w.error}` : w.description || '(无描述)' }}</div>
        <div class="meta">
          <span class="mono">{{ w.id }}</span>
          <span>{{ w.node_count ?? '-' }} 个节点</span>
        </div>
        <div class="ops">
          <el-button type="primary" :disabled="!!w.error" @click="$router.push(`/workflows/${w.id}/run`)">
            <el-icon><CaretRight /></el-icon><span>使用</span>
          </el-button>
          <el-button :disabled="!!w.error" @click="$router.push(`/workflows/${w.id}/edit`)">
            {{ w.builtin ? '查看 / 另存' : '编辑' }}
          </el-button>
          <el-button v-if="!w.builtin" class="del" text type="danger" @click="remove(w)"><el-icon><Delete /></el-icon></el-button>
        </div>
      </div>
      <div class="card new-card" @click="$router.push('/workflows/new/edit')">
        <el-icon :size="26"><Plus /></el-icon>
        <span>新建空白工作流</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CaretRight, Delete, Plus, Share } from '@element-plus/icons-vue'
import { deleteWorkflow, listWorkflows } from '../api.js'

const GRADIENTS = [
  'linear-gradient(135deg, #6366f1, #a855f7)', 'linear-gradient(135deg, #0ea5e9, #6366f1)',
  'linear-gradient(135deg, #f59e0b, #ef4444)', 'linear-gradient(135deg, #10b981, #0ea5e9)',
  'linear-gradient(135deg, #ec4899, #8b5cf6)',
]
const gradient = (i) => GRADIENTS[i % GRADIENTS.length]

const flows = ref([])
const loading = ref(false)

async function refresh() {
  loading.value = true
  try {
    flows.value = await listWorkflows()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function remove(w) {
  try {
    await ElMessageBox.confirm(`删除工作流「${w.title}」?已创建的任务不受影响。`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteWorkflow(w.id)
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error(e.message)
  }
  refresh()
}

onMounted(refresh)
</script>

<style scoped>
.page-head .el-button span, .ops .el-button span { margin-left: 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 18px; min-height: 120px; }
.card { display: flex; flex-direction: column; padding: 22px; transition: box-shadow 0.2s, transform 0.2s; }
.card.panel + .card.panel { margin-top: 0; }
.card.panel:hover { box-shadow: var(--vh-shadow-hover); transform: translateY(-2px); }
.card.broken { opacity: 0.7; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.icon { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center; color: #fff; }
.badge { font-size: 12px; padding: 3px 10px; border-radius: 999px; font-weight: 500; }
.badge.builtin { background: #f1f2f6; color: var(--vh-text-2); }
.badge.custom { background: #e8f6ed; color: #15803d; }
.title { font-weight: 700; font-size: 17px; }
.desc { color: var(--vh-text-2); font-size: 13.5px; margin: 8px 0 16px; line-height: 1.7; flex: 1; }
.meta { display: flex; justify-content: space-between; color: var(--vh-text-3); font-size: 12px; padding-top: 14px; border-top: 1px dashed var(--vh-border); }
.ops { margin-top: 16px; display: flex; align-items: center; }
.ops .del { margin-left: auto; }
.new-card {
  min-height: 240px; border: 2px dashed #d6d8e4; border-radius: var(--vh-radius); cursor: pointer;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
  color: var(--vh-text-3); transition: all 0.2s; padding: 22px;
}
.new-card:hover { border-color: var(--vh-primary); color: var(--vh-primary); background: var(--vh-primary-soft); }
</style>
