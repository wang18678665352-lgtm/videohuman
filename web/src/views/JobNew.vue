<template>
  <div class="page narrow">
    <div class="page-head">
      <div>
        <h1>新建数字人任务</h1>
        <p class="sub">选一种内容来源,配好声音与形象,一键生成口播视频。</p>
      </div>
    </div>

    <el-form :model="form" label-width="104px" label-position="left">
      <!-- 1. 内容来源 -->
      <section class="panel">
        <div class="panel-head"><span><b class="num">1</b>内容来源</span></div>
        <div class="panel-body">
          <div class="modes">
            <div v-for="m in MODES" :key="m.value" class="mode" :class="{ active: mode === m.value }" @click="mode = m.value">
              <div class="mode-icon"><el-icon :size="20"><component :is="m.icon" /></el-icon></div>
              <div class="mode-title">{{ m.title }}</div>
              <div class="mode-desc">{{ m.desc }}</div>
            </div>
          </div>

          <template v-if="mode === 'audio'">
            <el-form-item label="驱动音频">
              <el-select v-model="form.audio" placeholder="选择已有音频(assets/)" filterable @change="onAudioSelect">
                <el-option v-for="a in assets.audios" :key="a" :label="a" :value="a" />
              </el-select>
              <audio v-if="form.audio" :src="assetUrl(form.audio)" controls class="preview-audio" />
            </el-form-item>
            <el-form-item label="字幕文案">
              <div class="with-btn">
                <el-input v-model="form.text" type="textarea" :rows="3"
                  placeholder="【可选】字幕文案。留空则生成时自动从音频通过 ASR 识别或取同名 .txt" />
                <el-button :loading="audioAsrLoading" :disabled="!form.audio" @click="autoAudioText">识别文案</el-button>
              </div>
            </el-form-item>
          </template>
          <template v-else-if="mode === 'text'">
            <el-form-item label="口播文案">
              <el-input v-model="form.text" type="textarea" :rows="6"
                placeholder="直接输入口播文案(跳过下载/转写/改写前的环节,rewrite 直通或 LLM 改写)" />
              <div class="hint" v-if="textEstimate">
                文案约 {{ textEstimate.chars }} 字 → 预计音频约 {{ textEstimate.seconds }} 秒,视频时长跟随音频
              </div>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="抖音链接">
              <el-input v-model="form.share_url" :prefix-icon="Link"
                placeholder="如 https://www.douyin.com/jingxuan?modal_id=..." />
            </el-form-item>
          </template>
        </div>
      </section>

      <!-- 2. 声音 -->
      <section class="panel" v-if="mode !== 'audio'">
        <div class="panel-head"><span><b class="num">2</b>声音克隆</span></div>
        <div class="panel-body">
          <el-form-item label="TTS 引擎">
            <el-radio-group v-model="form.tts_engine">
              <el-radio-button value="indextts2">IndexTTS-2 · 推荐</el-radio-button>
              <el-radio-button value="cosyvoice3">CosyVoice3</el-radio-button>
            </el-radio-group>
            <div class="hint">IndexTTS-2 情感表达与时长控制更稳定</div>
          </el-form-item>
          <el-form-item label="参考音频">
            <el-select v-model="form.ref_audio" placeholder="选择音色参考(assets/)" filterable @change="autoRefText">
              <el-option v-for="a in assets.audios" :key="a" :label="a" :value="a" />
            </el-select>
          </el-form-item>
          <el-form-item label="参考音文本">
            <div class="with-btn">
              <el-input v-model="form.ref_text" type="textarea" :rows="2"
                placeholder="参考音频里说的原文;留空则生成时自动识别" />
              <el-button :loading="refTextLoading" :disabled="!form.ref_audio" @click="autoRefText(true)">自动识别</el-button>
            </div>
          </el-form-item>
        </div>
      </section>

      <!-- 3. 形象 -->
      <section class="panel">
        <div class="panel-head"><span><b class="num">{{ mode === 'audio' ? 2 : 3 }}</b>数字人形象</span></div>
        <div class="panel-body">
          <el-form-item label="数字人引擎">
            <el-radio-group v-model="form.avatar_engine">
              <el-radio-button value="musetalk">MuseTalk · 默认</el-radio-button>
              <el-radio-button value="lstmsync">LstmSync</el-radio-button>
            </el-radio-group>
            <div class="hint" v-if="form.avatar_engine === 'lstmsync'">需自备引擎,嘴部质量更好</div>
          </el-form-item>
          <el-form-item label="形象视频">
            <el-select v-model="form.avatar_video" placeholder="选择数字人素材(assets/)" filterable>
              <el-option v-for="v in assets.videos" :key="v" :label="v" :value="v" />
            </el-select>
            <video v-if="form.avatar_video" :key="form.avatar_video" :src="assetUrl(form.avatar_video)"
              class="preview-video" muted controls preload="metadata" />
          </el-form-item>
        </div>
      </section>

      <!-- 4. 后期与发布 -->
      <section class="panel">
        <div class="panel-head"><span><b class="num">{{ mode === 'audio' ? 3 : 4 }}</b>后期与发布</span></div>
        <div class="panel-body">
          <el-form-item label="后期处理">
            <div class="chips">
              <el-checkbox v-model="pp.silence_cut" border>静音切除</el-checkbox>
              <el-checkbox v-model="pp.subtitle" border>字幕烧录</el-checkbox>
              <el-checkbox v-model="pp.bgm" border>BGM 混音</el-checkbox>
              <el-checkbox v-model="pp.cover" border>封面生成</el-checkbox>
              <el-checkbox v-model="pp.bg_image" border>背景图合成</el-checkbox>
              <el-checkbox v-model="watermarkOn" border>文字水印</el-checkbox>
            </div>
            <el-input v-if="watermarkOn" v-model="pp.watermark_text" placeholder="水印文字" class="wm-input" />
            <div class="hint">默认与内核一致,一般不用动</div>
          </el-form-item>
          <el-form-item label="发布到">
            <el-checkbox-group v-model="form.publish_platforms">
              <el-checkbox value="douyin">抖音</el-checkbox>
              <el-checkbox value="bilibili">B站</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <template v-if="form.publish_platforms.length">
            <el-form-item label="发布标题">
              <el-input v-model="form.publish_title" placeholder="留空取文案首句" />
            </el-form-item>
            <el-form-item label="发布标签">
              <el-input v-model="form.publish_tags" placeholder="逗号分隔,可留空" />
            </el-form-item>
          </template>
          <el-form-item label="任务 ID">
            <el-input v-model="form.job_id" placeholder="留空自动生成 job-时间戳" />
          </el-form-item>
        </div>
      </section>

      <div class="submit-bar">
        <el-button size="large" @click="$router.push('/')">返回</el-button>
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
import { Link, Microphone, Document, VideoPlay } from '@element-plus/icons-vue'
import { assetUrl, createJob, getAssetRefText, getAssets, getSettings } from '../api.js'

const router = useRouter()
const MODES = [
  { value: 'audio', title: '直接对口型', desc: '已有录好的语音,跳过 TTS 直接驱动数字人', icon: Microphone },
  { value: 'text', title: '文案直给', desc: '输入口播文案,用克隆声音合成后生成视频', icon: Document },
  { value: 'clone', title: '抖音克隆', desc: '下载原片 → 转写 → 改写,用你的声音与形象重做', icon: Link },
]
const mode = ref('audio')
const submitting = ref(false)
const assets = reactive({ audios: [], videos: [], bgms: [] })
const watermarkOn = ref(false)
const refTextLoading = ref(false)
const audioAsrLoading = ref(false)

// 参考音文本自动识别:sidecar 文本秒回;无 sidecar 走 ASR(首次要加载模型,较慢)
async function autoRefText(force = false) {
  if (!form.ref_audio || refTextLoading.value) return
  if (!force && form.ref_text.trim()) return
  refTextLoading.value = true
  try {
    const r = await getAssetRefText(form.ref_audio)
    form.ref_text = r.text
    if (force) ElMessage.success(r.source === 'sidecar' ? '已读取同名文本' : '识别完成')
  } catch (e) {
    if (force) ElMessage.error(e.message)
  } finally {
    refTextLoading.value = false
  }
}

async function autoAudioText() {
  if (!form.audio || audioAsrLoading.value) return
  audioAsrLoading.value = true
  try {
    const r = await getAssetRefText(form.audio)
    form.text = r.text
    ElMessage.success(r.source === 'sidecar' ? '已读取同名文本' : '识别完成')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    audioAsrLoading.value = false
  }
}

function onAudioSelect() {
  if (form.audio && !form.text.trim()) {
    getAssetRefText(form.audio).then((r) => {
      if (r.source === 'sidecar' && !form.text.trim()) {
        form.text = r.text
      }
    }).catch(() => {})
  }
}

const form = reactive({
  job_id: '', audio: '', text: '', share_url: '',
  ref_audio: '', ref_text: '', avatar_video: '', avatar_engine: 'musetalk',
  tts_engine: 'indextts2',
  publish_platforms: [], publish_title: '', publish_tags: '',
})
const pp = reactive({
  silence_cut: true, bg_image: false, subtitle: true, bgm: true, cover: true,
  watermark_text: '',
})

// 文案长度 → 预估音频时长(实测中文口播约 6 字/秒);视频时长跟随音频
const textEstimate = computed(() => {
  const chars = form.text.replace(/\s/g, '').length
  if (!chars) return null
  return { chars, seconds: Math.max(1, Math.round(chars / 6)) }
})

async function submit() {
  if (mode.value === 'audio' && !form.audio) return ElMessage.warning('请选择驱动音频')
  if (mode.value === 'text' && !form.text.trim()) return ElMessage.warning('请输入文案')
  if (mode.value === 'clone' && !form.share_url.trim()) return ElMessage.warning('请输入抖音链接')
  if (mode.value !== 'audio' && !form.ref_audio) return ElMessage.warning('请选择参考音频')
  if (!form.avatar_video) return ElMessage.warning('请选择形象视频')
  submitting.value = true
  try {
    const payload = {
      avatar_engine: form.avatar_engine,
      avatar_video: form.avatar_video,
      postprocess: { ...pp, watermark_text: watermarkOn.value ? pp.watermark_text : null },
    }
    if (form.job_id.trim()) payload.job_id = form.job_id.trim()
    if (mode.value === 'audio') {
      payload.audio = form.audio
      if (form.text.trim()) payload.text = form.text.trim()
    } else if (mode.value === 'text') {
      payload.text = form.text
      payload.tts_engine = form.tts_engine
      payload.ref_audio = form.ref_audio
      if (form.ref_text.trim()) payload.ref_text = form.ref_text.trim()
    } else {
      payload.share_url = form.share_url.trim()
      payload.tts_engine = form.tts_engine
      payload.ref_audio = form.ref_audio
      if (form.ref_text.trim()) payload.ref_text = form.ref_text.trim()
    }
    if (form.publish_platforms.length) {
      payload.publish_platforms = form.publish_platforms
      if (form.publish_title.trim()) payload.publish_title = form.publish_title.trim()
      if (form.publish_tags.trim()) {
        payload.publish_tags = form.publish_tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
      }
    }
    const r = await createJob(payload)
    ElMessage.success(`任务 ${r.job_id} 已入队`)
    router.push(`/jobs/${r.job_id}`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  const [a, s] = await Promise.all([getAssets(), getSettings()])
  Object.assign(assets, a)
  form.avatar_engine = s.default_avatar_engine
  if (a.audios.includes('assets/录音机-20261005-1322.m4a')) {
    form.audio = 'assets/录音机-20261005-1322.m4a'
  } else if (a.audios.includes('assets/speech.wav')) {
    form.audio = 'assets/speech.wav'
  } else if (a.audios.length) {
    form.audio = a.audios[0]
  }

  if (a.audios.includes('assets/ref_voice.wav')) form.ref_audio = 'assets/ref_voice.wav'
  else if (a.audios.length) form.ref_audio = a.audios[0]

  if (a.videos.includes('assets/avatar_v3.mp4')) form.avatar_video = 'assets/avatar_v3.mp4'
  else if (a.videos.includes('assets/avatar_real_v3.mp4')) form.avatar_video = 'assets/avatar_real_v3.mp4'
  else if (a.videos.length) form.avatar_video = a.videos[0]

  if (mode.value === 'audio') {
    onAudioSelect()
  } else {
    autoRefText()
  }
})
</script>

<style scoped>
section.panel { margin-bottom: 16px; }
section.panel + section.panel { margin-top: 0; }
.num {
  display: inline-grid; place-items: center; width: 22px; height: 22px; margin-right: 10px; border-radius: 7px;
  background: var(--vh-primary-soft); color: var(--vh-primary); font-size: 12px;
}
.panel-body :deep(.el-form-item:last-child) { margin-bottom: 0; }
.panel-body :deep(.el-select) { width: 100%; }

.modes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 22px; }
.mode {
  padding: 16px; border: 1.5px solid var(--vh-border); border-radius: 12px; cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.mode:hover { border-color: var(--el-color-primary-light-5); }
.mode.active { border-color: var(--vh-primary); background: #f8f8ff; box-shadow: 0 0 0 3px var(--vh-primary-soft); }
.mode-icon {
  width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; margin-bottom: 10px;
  background: #f1f2f6; color: var(--vh-text-2);
}
.mode.active .mode-icon { background: linear-gradient(135deg, #6366f1, #a855f7); color: #fff; }
.mode-title { font-weight: 600; font-size: 14.5px; }
.mode-desc { color: var(--vh-text-3); font-size: 12px; margin-top: 4px; line-height: 1.55; }

.with-btn { display: flex; gap: 10px; width: 100%; align-items: flex-start; }
.with-btn .el-textarea { flex: 1; }
.preview-audio { width: 100%; height: 40px; margin-top: 10px; }
.preview-video { margin-top: 10px; max-width: 280px; max-height: 200px; border-radius: 10px; background: #000; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chips .el-checkbox { margin-right: 0; }
.wm-input { margin-top: 10px; max-width: 280px; }
.hint { width: 100%; }

.submit-bar {
  position: sticky; bottom: 0; z-index: 5; display: flex; justify-content: flex-end; gap: 10px;
  padding: 16px 0; background: linear-gradient(to top, var(--vh-bg) 70%, transparent);
}
.submit-bar .el-button span { margin-left: 6px; }

@media (max-width: 768px) {
  .modes { grid-template-columns: 1fr; }
  .with-btn { flex-direction: column; }
}
</style>
