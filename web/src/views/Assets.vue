<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>素材与设置</h1>
        <p class="sub">管理参考音频、形象视频等素材,配置发布平台登录与 LLM 改写。</p>
      </div>
    </div>

    <div class="layout">
      <div class="panel">
        <div class="panel-head">
          <span>素材库 <span class="muted mono">assets/</span></span>
          <el-upload :show-file-list="false" :http-request="(o) => doUpload(o, cur.kind)" :accept="cur.accept">
            <el-button type="primary"><el-icon><Upload /></el-icon><span>上传{{ cur.label }}</span></el-button>
          </el-upload>
        </div>
        <div class="tabs">
          <div v-for="t in tabs" :key="t.kind" class="tab" :class="{ active: tab === t.kind }" @click="tab = t.kind">
            <el-icon><component :is="t.icon" /></el-icon>{{ t.label }}
            <span class="count">{{ (assets[t.key] || []).length }}</span>
          </div>
        </div>
        <div class="files">
          <div v-for="row in assets[cur.key]" :key="row" class="file">
            <div class="file-icon"><el-icon><component :is="cur.icon" /></el-icon></div>
            <span class="file-name mono" :title="row">{{ row }}</span>
            <audio v-if="cur.kind === 'audio' || cur.kind === 'bgm'" :src="assetUrl(row)" controls preload="none" class="file-audio" />
            <el-image v-else-if="cur.kind === 'broll' && /\.(png|jpe?g|webp)$/i.test(row)" :src="assetUrl(row)"
              fit="cover" class="file-thumb" :preview-src-list="[assetUrl(row)]" preview-teleported />
          </div>
          <div v-if="!(assets[cur.key] || []).length" class="empty">
            <el-icon :size="36"><FolderOpened /></el-icon>
            <p>还没有{{ cur.label }},点右上角上传</p>
          </div>
        </div>
      </div>

      <div class="side">
        <div class="panel">
          <div class="panel-head">平台登录</div>
          <div class="panel-body">
            <div class="set-row">
              <div class="plat douyin">抖</div>
              <div class="set-main">
                <div class="set-title">抖音</div>
                <span class="status-pill" :class="settings.douyin_cookie_exists ? 'success' : ''">
                  {{ settings.douyin_cookie_exists ? '已登录' : '未登录' }}
                </span>
              </div>
              <el-button size="small" type="primary" plain :loading="loginState.status === 'running'" @click="doLogin">
                扫码登录
              </el-button>
            </div>
            <div v-if="loginState.status === 'running'" class="hint">浏览器已打开抖音,请扫码完成登录…</div>
            <div v-if="loginState.status === 'failed'" class="err">登录失败: {{ loginState.error }}</div>
            <div v-if="loginState.status === 'done'" class="ok">登录成功,cookie 已保存</div>
            <div class="set-row">
              <div class="plat bili">B</div>
              <div class="set-main">
                <div class="set-title">B站</div>
                <span class="status-pill" :class="settings.bilibili_cookie_exists ? 'success' : ''">
                  {{ settings.bilibili_cookie_exists ? '已配置' : '未配置' }}
                </span>
              </div>
            </div>
            <div class="hint">B站需手动放 <span class="mono">bilibili.json</span> 到 <span class="mono">configs/cookies/</span></div>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head">LLM 改写</div>
          <div class="panel-body">
            <div class="set-row">
              <div class="plat llm"><el-icon><MagicStick /></el-icon></div>
              <div class="set-main">
                <div class="set-title">API Key</div>
                <span class="status-pill" :class="settings.llm_configured ? 'success' : 'warning'">
                  {{ settings.llm_configured ? '已配置' : '未配置 · rewrite 直通' }}
                </span>
              </div>
            </div>
            <div class="hint">在 <span class="mono">configs/default.toml</span> 或环境变量 <span class="mono">VH_LLM_API_KEY</span> 配置</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FolderOpened, Headset, MagicStick, Microphone, Picture, Upload, VideoCamera,
} from '@element-plus/icons-vue'
import { assetUrl, getAssets, getSettings, loginDouyin, loginDouyinStatus, uploadAsset } from '../api.js'

const tab = ref('audio')
const tabs = [
  { kind: 'audio', key: 'audios', label: '参考音频', accept: 'audio/*', icon: Microphone },
  { kind: 'video', key: 'videos', label: '形象视频', accept: 'video/*', icon: VideoCamera },
  { kind: 'bgm', key: 'bgms', label: 'BGM', accept: 'audio/*', icon: Headset },
  { kind: 'broll', key: 'brolls', label: '混剪素材', accept: 'image/*,video/*', icon: Picture },
]
const cur = computed(() => tabs.find((t) => t.kind === tab.value))
const assets = reactive({ audios: [], videos: [], bgms: [], brolls: [] })
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
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 20px; align-items: start; }
.side .panel + .panel { margin-top: 16px; }
.panel-head .el-button span { margin-left: 6px; }

.tabs { display: flex; gap: 4px; padding: 10px 14px 0; border-bottom: 1px solid var(--vh-border); overflow-x: auto; }
.tab {
  display: flex; align-items: center; gap: 6px; padding: 10px 12px; font-size: 13.5px; cursor: pointer;
  color: var(--vh-text-2); border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap;
}
.tab:hover { color: var(--vh-text); }
.tab.active { color: var(--vh-primary); border-bottom-color: var(--vh-primary); font-weight: 600; }
.count { background: #f1f2f6; color: var(--vh-text-3); font-size: 11px; border-radius: 999px; padding: 0 7px; line-height: 18px; }
.tab.active .count { background: var(--vh-primary-soft); color: var(--vh-primary); }

.files { padding: 8px 12px 12px; max-height: 620px; overflow-y: auto; }
.file { display: flex; align-items: center; gap: 12px; padding: 8px; border-radius: 10px; }
.file:hover { background: #f7f8fb; }
.file-icon {
  width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0; display: grid; place-items: center;
  background: var(--vh-primary-soft); color: var(--vh-primary);
}
.file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--vh-text); }
.file-audio { height: 32px; width: 240px; flex-shrink: 0; }
.file-thumb { width: 72px; height: 44px; border-radius: 6px; flex-shrink: 0; }
.empty { padding: 48px 0; text-align: center; color: var(--vh-text-3); }
.empty p { margin: 8px 0 0; }

.set-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
.set-main { flex: 1; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.set-title { font-weight: 600; font-size: 14px; }
.plat {
  width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; color: #fff;
  font-weight: 700; font-size: 15px; flex-shrink: 0;
}
.plat.douyin { background: linear-gradient(135deg, #111, #333); box-shadow: inset -3px 0 0 #fe2c55, inset 3px 0 0 #25f4ee; }
.plat.bili { background: #00a1d6; }
.plat.llm { background: linear-gradient(135deg, #6366f1, #a855f7); }
.hint { margin: 0 0 8px; }
.err { color: var(--el-color-danger); font-size: 12px; margin-bottom: 8px; }
.ok { color: var(--el-color-success); font-size: 12px; margin-bottom: 8px; }

@media (max-width: 1080px) {
  .layout { grid-template-columns: 1fr; }
  .file-audio { width: 160px; }
}
</style>
