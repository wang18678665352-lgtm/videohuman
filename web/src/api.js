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
export const loginDouyinStatus = () => http.get('/login/douyin/status')

export const fileUrl = (jobId, path) =>
  withToken(`/api/jobs/${encodeURIComponent(jobId)}/file?path=${encodeURIComponent(path)}`)

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
  return http.post(`/assets?kind=${kind}`, fd, { timeout: 0 })
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
