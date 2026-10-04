<template>
  <a-layout class="app-layout">
    <a-layout-sider
      v-model:collapsed="collapsed"
      collapsible
      :width="210"
      theme="dark"
      class="app-sider"
    >
      <div class="sider-inner">
        <div class="logo">
          <span v-if="!collapsed" class="logo-text">📜 家谱</span>
          <span v-else>📜</span>
        </div>
        <a-menu theme="dark" mode="inline" :selected-keys="[route.name as string]" :default-open-keys="['shares']" class="sider-menu">
          <a-menu-item v-if="can('dashboard')" key="dashboard" @click="router.push('/dashboard')">
            <template #icon><span>📊</span></template>
            <span>仪表盘/大屏</span>
          </a-menu-item>
          <a-menu-item v-if="can('lineages')" key="lineages" @click="router.push('/lineages')">
            <template #icon><span>📚</span></template>
            <span>谱系管理</span>
          </a-menu-item>
          <a-menu-item v-if="can('documents')" key="documents" @click="router.push('/documents')">
            <template #icon><span>🗄️</span></template>
            <span>AI 识别归档</span>
          </a-menu-item>
          <a-menu-item v-if="can('tasks')" key="tasks" @click="router.push('/tasks')">
            <template #icon><span>🤖</span></template>
            <span>扫描件导入</span>
          </a-menu-item>
          <!-- 分享访问：3D 谱系 + 谱系分享(原图谱树分享) + 大屏分享 -->
          <a-sub-menu
            v-if="
              auth.canEdit &&
              (can('tree') || can('dashboard') || can('visit') || canVisit('visit_3d') || canVisit('visit_share') || canVisit('visit_dash'))
            "
            key="shares"
          >
            <template #icon><span>🔗</span></template>
            <template #title><span>分享访问</span></template>
            <a-menu-item v-if="can('tree') || canVisit('visit_3d')" key="tree3d" @click="router.push('/tree3d')">
              <template #icon><span>🧊</span></template>
              <span>3D 谱系</span>
            </a-menu-item>
            <a-menu-item v-if="canVisit('visit_share')" key="visits" @click="router.push('/visits')">
              <span>谱系分享</span>
            </a-menu-item>
            <a-menu-item v-if="can('dashboard') || canVisit('visit_dash')" key="dash-shares" @click="router.push('/dash-shares')">
              <span>大屏分享</span>
            </a-menu-item>
          </a-sub-menu>
          <a-menu-item v-if="auth.isAdmin" key="settings" @click="router.push('/settings')">
            <template #icon><span>⚙️</span></template>
            <span>
              系统设置
              <a-badge v-if="deleteApprovalCount.pending > 0" :count="deleteApprovalCount.pending" :overflow-count="99" class="menu-del-badge" />
            </span>
          </a-menu-item>
        </a-menu>
        <!-- 版权固定在左侧菜单栏底部，保证各页面位置一致 -->
        <AppCopyright v-if="!collapsed" mode="dark" />
      </div>
    </a-layout-sider>
    <a-layout class="right-area">
      <a-layout-header class="header">
        <span class="header-title">{{ (route.meta.title as string) || '' }}</span>
        <a-space>
          <span v-if="buildTimeText" class="build-tag" :title="buildTip">{{ buildTimeText }}</span>
          <span v-if="loginAtText" class="login-at" title="本次登录时间">{{ loginAtText }}</span>
          <!-- 提醒铃铛：有待办时直接显示提醒文字（无需鼠标悬停）；暂无时只留铃铛。
               结构按「多类提醒」预留：新提醒只需在 reminderList 追加条目 -->
          <a-tooltip
            v-if="auth.isAdmin"
            :title="reminderTotal > 0 ? `共 ${reminderTotal} 条待办提醒，点击进入处理` : '暂无新提醒'"
          >
            <span class="bell-wrap">
              <a-badge :count="reminderTotal" :overflow-count="99" :offset="[-6, 6]">
                <a
                  class="bell-btn"
                  :class="{ 'no-alert': reminderTotal === 0 }"
                  @click="bellClick"
                >🔔</a>
              </a-badge>
              <span v-if="reminderList.length" class="bell-inline-list">
                <a v-for="r in reminderList" :key="r.key" class="bell-inline" @click="openReminder(r)">
                  <span class="bell-dot" />
                  <span>{{ r.text }}</span>
                </a>
              </span>
            </span>
          </a-tooltip>
          <a-dropdown>
          <a-space class="user-info">
            <a-avatar size="small" style="background: #1677ff">
              {{ (auth.user?.full_name || auth.user?.username || '?').slice(0, 1) }}
            </a-avatar>
            <span>{{ auth.user?.full_name || auth.user?.username }}</span>
            <span class="role-tag">{{ roleText(auth.user?.role) }}</span>
          </a-space>
          <template #overlay>
            <a-menu>
              <a-menu-item key="password" @click="openPassword">修改密码</a-menu-item>
              <a-menu-item key="logout" @click="logout">退出登录</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        </a-space>
      </a-layout-header>
      <a-layout-content ref="contentRef" class="content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>

  <a-modal v-model:open="pwdOpen" title="修改密码" @ok="changePwd" :confirm-loading="pwdLoading">
    <a-form layout="vertical">
      <a-form-item label="原密码">
        <a-input-password v-model:value="oldPwd" autocomplete="off" />
      </a-form-item>
      <a-form-item label="新密码">
        <a-input-password v-model:value="newPwd" autocomplete="off" />
        <div class="pwd-rule">{{ PWD_RULE_TEXT }}</div>
      </a-form-item>
      <a-form-item label="确认新密码">
        <a-input-password v-model:value="confirmPwd" autocomplete="off" />
      </a-form-item>
    </a-form>
  </a-modal>

  <!-- 操作员首次登录/密码被重置后：强制改密全屏层，改完才能操作 -->
  <div v-if="mustChange" class="force-pwd-mask">
    <div class="force-pwd-card">
      <h3>🔒 请先修改初始密码</h3>
      <p class="force-tip">为保障账号安全，首次登录（或密码被重置后）须先修改密码，才能继续使用系统。</p>
      <a-form layout="vertical">
        <a-form-item label="原密码">
          <a-input-password v-model:value="oldPwd" autocomplete="off" />
        </a-form-item>
        <a-form-item label="新密码">
          <a-input-password v-model:value="newPwd" autocomplete="off" />
          <div class="pwd-rule">{{ PWD_RULE_TEXT }}</div>
        </a-form-item>
        <a-form-item label="确认新密码">
          <a-input-password v-model:value="confirmPwd" autocomplete="off" />
        </a-form-item>
      </a-form>
      <a-button type="primary" block :loading="pwdLoading" @click="forceChangePwd">修改并继续</a-button>
      <a-button class="force-logout" block @click="logout">退出登录</a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'
import { changePasswordApi } from '@/api'
import { setUser } from '@/utils/token'
import { deleteApprovalCount, refreshDeleteApprovalCount } from '@/utils/deleteApprovalCount'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const can = (code: string) => auth.canAccessModule(code)
/** 分享访问子项：勾了父项 visit 即等价于三项全开，也可只勾某一子项 */
const canVisit = (sub: string) => can('visit') || can(sub)
const collapsed = ref(false)

// 右侧内容区为独立滚动容器（外壳固定视口高）；切页时回到顶部，避免新页接续旧页滚动位置
const contentRef = ref<HTMLElement | null>(null)
watch(
  () => route.path,
  () => {
    const el = contentRef.value
    if (el) el.scrollTop = 0
  },
)

const pwdOpen = ref(false)
const oldPwd = ref('')
const newPwd = ref('')
const confirmPwd = ref('')
const pwdLoading = ref(false)

// 密码强度规则（与后端一致）：弱密码=≤6 位 或 字符类型少于 2 种（大写/小写/数字/符号）
const PWD_RULE_TEXT = '至少 7 位，且包含至少两种字符（大写/小写字母、数字、符号）'
const pwdCharKinds = (p: string) => {
  let kinds = 0
  if (/[a-z]/.test(p)) kinds += 1
  if (/[A-Z]/.test(p)) kinds += 1
  if (/\d/.test(p)) kinds += 1
  if (/[^A-Za-z0-9]/.test(p)) kinds += 1
  return kinds
}
const isWeakPwd = (p: string) => p.length <= 6 || pwdCharKinds(p) < 2

const roleText = (r?: string) =>
  r === 'admin' ? '管理员' : r === 'editor' ? '操作员' : r === 'viewer' ? '只读' : ''

// ============ 构建版本角标（vite 构建时注入 __BUILD_VERSION__/__BUILD_TIME__，每次发布构建版本号自动 +1） ============
declare const __BUILD_VERSION__: string
declare const __BUILD_TIME__: string
const buildTimeText = (() => {
  try {
    const verRaw = (typeof __BUILD_VERSION__ !== 'undefined' && __BUILD_VERSION__) || ''
    const ver = Number(verRaw)
    const verText = Number.isFinite(ver) && ver > 0 ? `版本 v${ver}` : ''
    const raw = (typeof __BUILD_TIME__ !== 'undefined' && __BUILD_TIME__) || ''
    if (!raw) return verText
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return verText
    const p = (n: number) => String(n).padStart(2, '0')
    const ts = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
    return verText ? `${verText} · ${ts}` : ts
  } catch {
    return ''
  }
})()
const buildTip = '版本号每次发布自动 +1；时间为该版本的构建时刻（若早于最新部署时间，说明浏览器还在用旧版缓存，请强刷页面）'

// ============ 本次登录时间角标（与「记住我」会话一起存取） ============
const loginAtText = computed(() => {
  const t = auth.loginAt
  if (!t) return ''
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `登录 ${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
})

// ============ 空闲自动登出（分钟；0 表示不启用）============
const IDLE_MINUTES = Number((import.meta as any).env?.VITE_IDLE_TIMEOUT_MINUTES ?? 60)
const ACTIVITY_EVENTS = ['click', 'keydown', 'mousemove', 'scroll', 'touchstart']
let lastActive = Date.now()
let idleTimer: number | undefined

const markActive = () => {
  lastActive = Date.now()
}

const checkIdle = () => {
  if (!IDLE_MINUTES || !auth.isLoggedIn) return
  if (Date.now() - lastActive >= IDLE_MINUTES * 60 * 1000) {
    auth.logout()
    message.warning(`已超过 ${IDLE_MINUTES} 分钟无操作，为安全起见已自动退出登录`)
    router.push('/login')
  }
}

onMounted(() => {
  if (auth.user?.role === 'admin') startApprovalPoll()
  if (!IDLE_MINUTES) return
  ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, markActive, { passive: true }))
  idleTimer = window.setInterval(checkIdle, 30 * 1000)
})

onBeforeUnmount(() => {
  ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, markActive))
  if (idleTimer) window.clearInterval(idleTimer)
  window.removeEventListener('delete-approval-changed', onApprovalChanged)
  if (approvalTimer) window.clearInterval(approvalTimer)
})

const logout = () => {
  auth.logout()
  router.push('/login')
}

// ============ 提醒中心（铃铛 + 直接可见文字；后续新提醒在此追加条目） ============
// 每类提醒一项：text=直接显示在铃铛旁的提醒文字，count=该类条数，to=点击去处
interface ReminderItem {
  key: string
  text: string
  count: number
  to: { path: string; query: { tab: string } }
}

let approvalTimer: number | undefined

const bellClick = () => {
  // 仅当存在待办提醒时，点击铃铛才跳转到对应处理页；无提醒时点击无反应
  const first = reminderList.value[0]
  if (!first) return
  router.push(first.to)
}

const reminderList = computed<ReminderItem[]>(() => {
  const list: ReminderItem[] = []
  // 提醒一：操作员提交的删除申请待管理员审批
  const n = deleteApprovalCount.pending
  if (n > 0) {
    list.push({
      key: 'delete-approvals',
      text: `删除审批：操作员提交的删除申请待办`,
      count: n,
      to: { path: '/settings', query: { tab: 'delete-approvals' } },
    })
  }
  return list
})

const reminderTotal = computed(() => reminderList.value.reduce((s, r) => s + r.count, 0))

const openReminder = (r: ReminderItem) => {
  router.push(r.to)
}

const onApprovalChanged = () => {
  refreshDeleteApprovalCount(auth.user?.role)
}

const startApprovalPoll = () => {
  refreshDeleteApprovalCount(auth.user?.role)
  window.addEventListener('delete-approval-changed', onApprovalChanged)
  approvalTimer = window.setInterval(() => refreshDeleteApprovalCount(auth.user?.role), 60_000)
}

const openPassword = () => {
  oldPwd.value = newPwd.value = confirmPwd.value = ''
  pwdOpen.value = true
}

const changePwd = async () => {
  if (newPwd.value !== confirmPwd.value) {
    message.error('两次输入的新密码不一致')
    return
  }
  if (newPwd.value.length < 3) {
    message.error('新密码至少 3 位')
    return
  }
  pwdLoading.value = true
  try {
    await changePasswordApi(oldPwd.value, newPwd.value)
    message.success('密码已修改')
    pwdOpen.value = false
  } finally {
    pwdLoading.value = false
  }
}

// ============ 操作员强制改密（首次登录/密码被重置后） ============
const mustChange = computed(() => !!auth.user?.must_change_password)

const forceChangePwd = async () => {
  if (!oldPwd.value) {
    message.error('请输入原密码')
    return
  }
  if (newPwd.value !== confirmPwd.value) {
    message.error('两次输入的新密码不一致')
    return
  }
  if (isWeakPwd(newPwd.value)) {
    message.error(`新密码强度不足：${PWD_RULE_TEXT}`)
    return
  }
  pwdLoading.value = true
  try {
    await changePasswordApi(oldPwd.value, newPwd.value)
    message.success('密码已修改，请牢记新密码')
    if (auth.user) {
      auth.user.must_change_password = false
      setUser(auth.user, auth.remember)
    }
    oldPwd.value = newPwd.value = confirmPwd.value = ''
  } finally {
    pwdLoading.value = false
  }
}
</script>

<style scoped>
/* 外壳固定为视口高：左侧菜单/顶栏钉住，仅右侧 .content 内部滚动（否则长页会把整体顶高、菜单滚出屏幕） */
.app-layout {
  height: 100vh;
  overflow: hidden;
}
.right-area {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.app-sider :deep(.ant-layout-sider-children) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.app-sider :deep(.ant-layout-sider-trigger) {
  flex: none;
}
.sider-inner {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 600;
  font-size: 16px;
  background: rgba(255, 255, 255, 0.08);
  flex: none;
}
.logo-text {
  white-space: nowrap;
}
.sider-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.header {
  background: #fff;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
.header-title {
  font-size: 17px;
  font-weight: 600;
}
.user-info {
  cursor: pointer;
}
.role-tag {
  font-size: 12px;
  color: #1677ff;
  border: 1px solid #91caff;
  border-radius: 4px;
  padding: 0 6px;
  background: #e6f4ff;
}
.build-tag {
  font-size: 11px;
  color: #c0b8a8;
  cursor: default;
  user-select: none;
}
.login-at {
  font-size: 12px;
  color: #8a94a6;
  cursor: default;
  user-select: none;
  padding-left: 10px;
  border-left: 1px solid #e4e7ec;
}
.bell-btn {
  font-size: 17px;
  text-decoration: none;
  cursor: pointer;
  display: inline-block;
  padding: 0 2px;
}
.bell-btn.no-alert {
  cursor: default;
  opacity: 0.45;
}
.bell-wrap {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.bell-inline-list {
  display: inline-flex;
  align-items: center;
  max-width: 420px;
  overflow: hidden;
}
.bell-inline {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #d4380d;
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
  line-height: 1;
}
.bell-inline:hover {
  color: #ad2102;
}
.bell-inline + .bell-inline {
  margin-left: 6px;
}
.bell-inline + .bell-inline::before {
  content: '';
  width: 1px;
  height: 10px;
  background: #e4e7ec;
  margin-right: 6px;
}
.bell-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ff4d4f;
  flex: 0 0 auto;
}
.menu-del-badge {
  margin-left: 6px;
}
.content {
  flex: 1 1 auto;
  min-height: 0;
  padding: 20px;
  overflow: auto;
}
.force-pwd-mask {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
}
.force-pwd-card {
  width: 360px;
  max-width: calc(100vw - 40px);
  background: #fff;
  border-radius: 10px;
  padding: 24px 24px 20px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
}
.force-pwd-card h3 {
  margin: 0 0 8px;
  font-size: 17px;
}
.force-pwd-card .force-tip {
  color: #888;
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 16px;
}
.force-logout {
  margin-top: 8px;
}
.pwd-rule {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}
</style>
