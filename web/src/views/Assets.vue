<template>
  <div>
    <h2>素材与设置</h2>
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card header="素材库(assets/)">
          <el-tabs v-model="tab">
            <el-tab-pane v-for="t in tabs" :key="t.kind" :label="t.label" :name="t.kind">
              <el-table :data="assets[t.key]" size="small">
                <el-table-column label="文件">
                  <template #default="{ row }">{{ row }}</template>
                </el-table-column>
                <el-table-column width="120">
                  <template #default="{ row }">
                    <audio v-if="t.kind !== 'video'" :src="assetUrl(row)" controls style="height: 28px; width: 110px" />
                  </template>
                </el-table-column>
              </el-table>
              <el-upload :show-file-list="false" :http-request="(o) => doUpload(o, t.kind)" :accept="t.accept" style="margin-top: 12px">
                <el-button type="primary" plain>上传{{ t.label }}</el-button>
              </el-upload>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card header="平台登录">
          <div class="set-row">
            <span>抖音</span>
            <el-tag :type="settings.douyin_cookie_exists ? 'success' : 'info'" size="small">
              {{ settings.douyin_cookie_exists ? '已登录' : '未登录' }}
            </el-tag>
            <el-button size="small" type="primary" :loading="loginState.status === 'running'" @click="doLogin">
              扫码登录
            </el-button>
          </div>
          <div v-if="loginState.status === 'running'" class="hint">浏览器已打开抖音,请扫码完成登录…</div>
          <div v-if="loginState.status === 'failed'" class="err">登录失败: {{ loginState.error }}</div>
          <div v-if="loginState.status === 'done'" class="ok">登录成功,cookie 已保存</div>
          <div class="set-row">
            <span>B站</span>
            <el-tag :type="settings.bilibili_cookie_exists ? 'success' : 'info'" size="small">
              {{ settings.bilibili_cookie_exists ? '已配置' : '未配置' }}
            </el-tag>
            <span class="hint">需手动放 bilibili.json 到 configs/cookies/</span>
          </div>
        </el-card>
        <el-card header="LLM 改写" style="margin-top: 16px">
          <div class="set-row">
            <span>API Key</span>
            <el-tag :type="settings.llm_configured ? 'success' : 'warning'" size="small">
              {{ settings.llm_configured ? '已配置' : '未配置(rewrite 直通)' }}
            </el-tag>
          </div>
          <div class="hint">在 configs/default.toml 或环境变量 VH_LLM_API_KEY 配置</div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assetUrl, getAssets, getSettings, loginDouyin, loginDouyinStatus, uploadAsset } from '../api.js'

const tab = ref('audio')
const tabs = [
  { kind: 'audio', key: 'audios', label: '参考音频', accept: 'audio/*' },
  { kind: 'video', key: 'videos', label: '形象视频', accept: 'video/*' },
  { kind: 'bgm', key: 'bgms', label: 'BGM', accept: 'audio/*' },
]
const assets = reactive({ audios: [], videos: [], bgms: [] })
const settings = reactive({})
const loginState = reactive({ status: 'idle', error: null })
let loginTimer = null

async function refresh() {
  Object.assign(assets, await getAssets())
  Object.assign(settings, await getSettings())
}

async function confirmConsent(kind, file) {
  const statement = settings.consent_statements?.[kind]
  if (!statement) return true
  try {
    await ElMessageBox.confirm(statement, `授权确认:${file.name}`, {
      type: 'warning', confirmButtonText: '我确认并上传', cancelButtonText: '取消',
    })
    return true
  } catch {
    return false
  }
}

async function doUpload(option, kind) {
  if (!(await confirmConsent(kind, option.file))) return
  try {
    await uploadAsset(option.file, kind)
    ElMessage.success('上传成功')
    refresh()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function doLogin() {
  await loginDouyin()
  pollLogin()
}

async function pollLogin() {
  const s = await loginDouyinStatus()
  Object.assign(loginState, s)
  if (s.status === 'running') {
    loginTimer = setTimeout(pollLogin, 2000)
  } else {
    refresh()
  }
}

onMounted(() => { refresh() })
onUnmounted(() => clearTimeout(loginTimer))
</script>

<style scoped>
.set-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
.hint { color: #909399; font-size: 12px; }
.err { color: #f56c6c; font-size: 12px; }
.ok { color: #67c23a; font-size: 12px; }
</style>
