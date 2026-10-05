<template>
  <div style="max-width: 760px">
    <h2>新建任务</h2>
    <el-form :model="form" label-width="110px">
      <el-form-item label="任务 ID">
        <el-input v-model="form.job_id" placeholder="留空自动生成 job-时间戳" style="width: 320px" />
      </el-form-item>

      <el-form-item label="内容来源">
        <el-tabs v-model="mode" style="width: 100%">
          <el-tab-pane label="直接对口型" name="audio">
            <div style="margin-bottom: 12px">
              <span style="display: inline-block; width: 80px; color: #606266">驱动音频</span>
              <el-select v-model="form.audio" placeholder="选择已有音频(assets/)" style="width: 420px" filterable @change="onAudioSelect">
                <el-option v-for="a in assets.audios" :key="a" :label="a" :value="a" />
              </el-select>
            </div>
            <div v-if="form.audio" style="margin-left: 80px; margin-bottom: 12px">
              <audio :src="assetUrl(form.audio)" controls style="height: 36px; width: 420px"></audio>
            </div>
            <div style="display: flex; align-items: flex-start; margin-bottom: 8px">
              <span style="display: inline-block; width: 80px; color: #606266; padding-top: 5px">字幕文案</span>
              <div>
                <el-input v-model="form.text" type="textarea" :rows="3"
                  placeholder="【可选】字幕文案。留空则生成时自动从音频通过 ASR 识别或取同名 .txt" style="width: 420px" />
                <el-button style="margin-left: 8px" :loading="audioAsrLoading" :disabled="!form.audio"
                  @click="autoAudioText">识别文案</el-button>
              </div>
            </div>
            <div class="hint" style="margin-left: 80px">已有录制好的语音，跳过 TTS 语音生成，直接使用原声驱动数字人对口型。</div>
          </el-tab-pane>
          <el-tab-pane label="文案直给" name="text">
            <el-input v-model="form.text" type="textarea" :rows="5"
              placeholder="直接输入口播文案(跳过下载/转写/改写前的环节,rewrite 直通或 LLM 改写)" />
            <div class="hint" v-if="textEstimate">文案约 {{ textEstimate.chars }} 字 → 预计音频约 {{ textEstimate.seconds }} 秒,视频时长跟随音频</div>
          </el-tab-pane>
          <el-tab-pane label="抖音克隆" name="clone">
            <el-input v-model="form.share_url"
              placeholder="抖音链接,如 https://www.douyin.com/jingxuan?modal_id=..." />
            <div class="hint">将下载原片 → 转写文案 → 改写,再用你的声音与形象重新生成</div>
          </el-tab-pane>
        </el-tabs>
      </el-form-item>

      <template v-if="mode !== 'audio'">
        <el-form-item label="TTS 引擎">
          <el-radio-group v-model="form.tts_engine">
            <el-radio value="indextts2">IndexTTS-2(情感表达与时长控制, 稳定推荐)</el-radio>
            <el-radio value="cosyvoice3">CosyVoice3</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="参考音频">
          <el-select v-model="form.ref_audio" placeholder="选择音色参考(assets/)" style="width: 420px"
            @change="autoRefText">
            <el-option v-for="a in assets.audios" :key="a" :label="a" :value="a" />
          </el-select>
        </el-form-item>
        <el-form-item label="参考音文本">
          <el-input v-model="form.ref_text" type="textarea" :rows="2"
            placeholder="参考音频里说的原文;留空则生成时自动识别" style="width: 420px" />
          <el-button style="margin-left: 8px" :loading="refTextLoading" :disabled="!form.ref_audio"
            @click="autoRefText(true)">自动识别</el-button>
        </el-form-item>
      </template>

      <el-form-item label="数字人引擎">
        <el-radio-group v-model="form.avatar_engine">
          <el-radio value="musetalk">musetalk(默认)</el-radio>
          <el-radio value="lstmsync">lstmsync(需自备引擎,嘴部质量更好)</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="形象视频">
        <el-select v-model="form.avatar_video" placeholder="选择数字人素材(assets/)" style="width: 420px">
          <el-option v-for="v in assets.videos" :key="v" :label="v" :value="v" />
        </el-select>
      </el-form-item>

      <el-form-item label="发布">
        <el-checkbox-group v-model="form.publish_platforms">
          <el-checkbox value="douyin">抖音</el-checkbox>
          <el-checkbox value="bilibili">B站</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <template v-if="form.publish_platforms.length">
        <el-form-item label="发布标题">
          <el-input v-model="form.publish_title" placeholder="留空取文案首句" style="width: 420px" />
        </el-form-item>
        <el-form-item label="发布标签">
          <el-input v-model="form.publish_tags" placeholder="逗号分隔,可留空" style="width: 420px" />
        </el-form-item>
      </template>

      <el-form-item label="后期开关">
        <el-collapse style="width: 100%">
          <el-collapse-item title="postprocess 六步(默认与内核一致,一般不用动)" name="pp">
            <el-checkbox v-model="pp.silence_cut">静音切除</el-checkbox>
            <el-checkbox v-model="pp.bg_image">背景图合成</el-checkbox>
            <el-checkbox v-model="pp.subtitle">字幕烧录</el-checkbox>
            <el-checkbox v-model="pp.bgm">BGM 混音</el-checkbox>
            <el-checkbox v-model="pp.cover">封面生成</el-checkbox>
            <div style="margin-top: 8px">
              <el-checkbox v-model="watermarkOn">文字水印</el-checkbox>
              <el-input v-if="watermarkOn" v-model="pp.watermark_text" placeholder="水印文字"
                style="width: 240px; margin-left: 12px" />
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="submitting" @click="submit">创建并执行</el-button>
        <el-button @click="$router.push('/')">返回</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assetUrl, createJob, getAssetRefText, getAssets, getSettings } from '../api.js'

const router = useRouter()
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
    if (force) ElMessage.error(e.message)
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
.hint { color: #909399; font-size: 12px; margin-top: 4px; }
</style>
