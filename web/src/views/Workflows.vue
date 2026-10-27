<template>
  <div>
    <div class="bar">
      <h2>工作流</h2>
      <el-button type="primary" @click="$router.push('/workflows/new/edit')">新建空白工作流</el-button>
    </div>
    <p class="intro">
      工作流把"输入 → 文本 → 音频 → 视频 → 后期 → 发布"各步骤自由连起来。内置模板可直接使用,
      也可以打开编辑器,复制一份后按需增删节点、改参数。
    </p>
    <el-row :gutter="16" v-loading="loading">
      <el-col v-for="w in flows" :key="w.id" :xs="24" :sm="12" :lg="8" class="col">
        <el-card shadow="hover" class="card">
          <div class="card-head">
            <span class="title">{{ w.title }}</span>
            <el-tag v-if="w.builtin" size="small" type="info">内置</el-tag>
            <el-tag v-else size="small" type="success">自定义</el-tag>
          </div>
          <div class="desc">{{ w.error ? `文件损坏:${w.error}` : w.description || '(无描述)' }}</div>
          <div class="meta">{{ w.id }} · {{ w.node_count ?? '-' }} 个节点</div>
          <div class="ops">
            <el-button type="primary" size="small" :disabled="!!w.error" @click="$router.push(`/workflows/${w.id}/run`)">
              使用
            </el-button>
            <el-button size="small" :disabled="!!w.error" @click="$router.push(`/workflows/${w.id}/edit`)">
              {{ w.builtin ? '查看 / 另存' : '编辑' }}
            </el-button>
            <el-button v-if="!w.builtin" size="small" type="danger" plain @click="remove(w)">删除</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteWorkflow, listWorkflows } from '../api.js'

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
.bar { display: flex; justify-content: space-between; align-items: center; }
.bar h2 { margin: 0; }
.intro { color: #606266; font-size: 13px; margin: 8px 0 16px; }
.col { margin-bottom: 16px; }
.card { height: 100%; }
.card-head { display: flex; align-items: center; gap: 8px; }
.title { font-weight: 600; font-size: 16px; }
.desc { color: #606266; font-size: 13px; margin: 10px 0; min-height: 40px; line-height: 1.6; }
.meta { color: #a8abb2; font-size: 12px; font-family: monospace; }
.ops { margin-top: 12px; }
</style>
