import { defineStore } from 'pinia'
import { loginApi, meApi } from '@/api'
import type { UserInfo } from '@/types'
import {
  clearAuth,
  getLoginAt,
  getToken,
  getUser,
  isRemembered,
  setLoginAt,
  setToken,
  setUser,
} from '@/utils/token'

interface AuthState {
  token: string
  user: UserInfo | null
  remember: boolean
  /** 本次登录成功的时间戳（毫秒） */
  loginAt: number | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: '',
    user: null,
    remember: false,
    loginAt: null,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'admin',
    canEdit: (s) => s.user?.role === 'admin' || s.user?.role === 'editor',
    /** 操作员按「可访问模块」判定；admin/只读角色不限制（只读角色能否编辑另由后端控制） */
    canAccessModule:
      (s) =>
      (codes: string | string[]): boolean => {
        const u = s.user
        if (!u) return false
        if (u.role === 'admin' || u.role === 'viewer') return true
        const list = Array.isArray(codes) ? codes : [codes]
        const perms = u.permissions
        if (!perms) return true // null=全部
        return list.some((c) => perms.includes(c))
      },
    /** 菜单顺序上第一个有权限的模块页；给守卫作默认落地页 */
    firstAllowedName: (s): string => {
      const u = s.user
      if (!u || u.role !== 'editor') return 'dashboard'
      if (!u.permissions) return 'dashboard'
      const order = ['dashboard', 'tree', 'lineages', 'documents', 'tasks', 'visit']
      const names: Record<string, string> = {
        dashboard: 'dashboard',
        // 2D 家谱树入口已下线；有 tree 权限的操作员默认落到 3D 谱系
        tree: 'tree3d',
        lineages: 'lineages',
        documents: 'documents',
        tasks: 'tasks',
        visit: 'visits',
      }
      const hit = order.find((c) => u.permissions!.includes(c))
      return names[hit || 'dashboard']
    },
  },
  actions: {
    /** 启动时恢复登录态（会话级，或「记住我」未过期） */
    restore() {
      this.token = getToken()
      this.user = getUser<UserInfo>()
      this.remember = isRemembered()
      this.loginAt = getLoginAt()
    },
    async login(
      username: string,
      password: string,
      remember = false,
      hotpCode: string,
    ) {
      // 动态码由登录页「可见」生成并人工输入：见 Login.vue getCode()（服务端 /auth/hotp-code 签发）
      const data = await loginApi({ username, password, remember, hotpCode })
      this.token = data.access_token
      this.remember = remember
      setToken(this.token, remember, data.expires_in)
      this.loginAt = Date.now()
      setLoginAt(this.loginAt, remember)
      await this.fetchMe()
    },
    async fetchMe() {
      this.user = await meApi()
      setUser(this.user, this.remember)
    },
    logout() {
      this.token = ''
      this.user = null
      this.remember = false
      this.loginAt = null
      clearAuth()
    },
  },
})
