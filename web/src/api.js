import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

export const getToken = () => localStorage.getItem('vh_token') || ''
export const setToken = (t) =>
  t ? localStorage.setItem('vh_token', t) : localStorage.removeItem('vh_token')

http.interceptors.request.use((cfg) => {
  const t = getToken()
  if (t) cfg.headers['X-VH-Token'] = t
  return cfg
})

http.interceptors.response.use(
  (r) => r.data,
  (e) => {
    if (e.response?.status === 401) {
      setToken('')
      if (!location.pathname.startsWith('/login')) {
        location.href = '/login?back=' + encodeURIComponent(location.pathname)
      }
    }
    const msg = e.response?.data?.detail || e.message
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  },
)

// 媒体标签(<audio>/<video>/<img>)无法设请求头,token 走 query 参数
const withToken = (url) => {
  const t = getToken()
  return t ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}` : url
}

export const getSettings = () => http.get('/settings')
export const getReadiness = () => http.get('/readiness')
export const getUsage = () => http.get('/usage')
export const getAssets = () => http.get('/assets')
export const getAssetRefText = (path) =>
  http.get('/asset-ref-text', { params: { path }, timeout: 120000 })
export const listJobs = () => http.get('/jobs')
export const getJob = (id) => http.get(`/jobs/${encodeURIComponent(id)}`)
export const createJob = (payload) => http.post('/jobs', payload)
export const runJob = (id, body) => http.post(`/jobs/${encodeURIComponent(id)}/run`, body)
export const cancelJob = (id) => http.post(`/jobs/${encodeURIComponent(id)}/cancel`)
export const deleteJob = (id) => http.delete(`/jobs/${encodeURIComponent(id)}`)
export const loginDouyin = () => http.post('/login/douyin')

// 工作流
export const listSteps = () => http.get('/steps')
export const listWorkflows = () => http.get('/workflows')
export const getWorkflow = (id) => http.get(`/workflows/${encodeURIComponent(id)}`)
export const saveWorkflow = (spec) => http.put(`/workflows/${encodeURIComponent(spec.id)}`, spec)
export const deleteWorkflow = (id) => http.delete(`/workflows/${encodeURIComponent(id)}`)
export const validateWorkflow = (spec) => http.post('/workflows/validate', spec)

// 端口类型的显示名与颜色(编辑器连线、节点端口共用)
export const KIND_META = {
  text: { label: '文本', color: '#909399' },
  audio: { label: '音频', color: '#e6a23c' },
  video: { label: '视频', color: '#409eff' },
  image: { label: '图片', color: '#67c23a' },
  subtitle: { label: '字幕', color: '#b37feb' },
  json: { label: '数据', color: '#606266' },
}
// 与后端 vh_core.pipeline.kinds_compatible 一致:视频可接到音频输入
export const kindsCompatible = (src, dst) => src === dst || (dst === 'audio' && src === 'video')
export const loginDouyinStatus = () => http.get('/login/douyin/status')

export const fileUrl = (jobId, path) =>
  withToken(`/api/jobs/${encodeURIComponent(jobId)}/file?path=${encodeURIComponent(path)}`)

export const deliveryUrl = (jobId) =>
  withToken(`/api/jobs/${encodeURIComponent(jobId)}/delivery`)

export const consentUrl = () => withToken('/api/consent')

export const assetUrl = (path) =>
  withToken(`/api/asset-file?path=${encodeURIComponent(path)}`)

export const fetchText = (jobId, path) =>
  axios
    .get(fileUrl(jobId, path), { responseType: 'text' })
    .then((r) => r.data)

export function uploadAsset(file, kind) {
  const fd = new FormData()
  fd.append('file', file)
  // 大视频上传可能很慢,不套用默认 30s 超时
  // consent=true 表示用户已在界面上确认授权声明;服务端会存档该确认
  return http.post(`/assets?kind=${kind}&consent=true`, fd, { timeout: 0 })
}

// 状态展示统一在这里定义,列表页与详情页共用
export const STATUS_TEXT = {
  done: '完成', failed: '失败', running: '运行中', queued: '排队中', interrupted: '中断',
  cancelling: '取消中', cancelled: '已取消', unknown: '未知',
}
export const STATUS_TYPE = {
  done: 'success', failed: 'danger', running: 'primary', queued: 'info', cancelling: 'warning', cancelled: 'info',
}
export const isActive = (s) => s === 'running' || s === 'queued' || s === 'cancelling'
