import axios from 'axios'
import { message } from 'ant-design-vue'
import { clearAuth, getToken } from '@/utils/token'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 300000, // AI 推理可能慢
})

request.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    // 业务侧已自行展示错误时跳过自动 toast（登录页浮窗内自管提示）
    const silent = (error.config as any)?.silentError === true
    if (silent) return Promise.reject(error)
    if (status === 401) {
      // 访客分享页（根路径短链 /<code> 与 /d<code>，由 ShareEntry 设置标记；旧 /visit、/s/ 兜底）：不跳登录，仅提示
      const isVisitPage =
        (window as any).__isSharePage ||
        window.location.pathname.startsWith('/visit') ||
        window.location.pathname.startsWith('/s/')
      if (isVisitPage) {
        message.error(detail || '访客链接无效或已过期')
      } else {
        // 被别处登录挤下线（后端单点登录校验）时给出明确原因，而非笼统的「登录已失效」
        const kicked = typeof detail === 'string' && detail.includes('其他位置登录')
        clearAuth()
        const redirect = encodeURIComponent(window.location.pathname + window.location.search)
        window.location.href = `/login?redirect=${redirect}`
        message.error(kicked ? '账号已在其他位置登录，本机已退出登录' : '登录已失效，请重新登录')
      }
    } else if (status && status !== 404) {
      // detail 可能是字符串（普通错误）或数组（422 验证错误），统一转成可读文本
      let msg = '请求失败'
      if (typeof detail === 'string' && detail) {
        msg = detail
      } else if (Array.isArray(detail) && detail.length) {
        msg = detail.map((d: any) => d?.msg || JSON.stringify(d)).join('；')
      } else if (detail && typeof detail === 'object') {
        msg = JSON.stringify(detail)
      } else {
        msg = error.message || '请求失败'
      }
      message.error(msg)
    }
    return Promise.reject(error)
  },
)

export default request
