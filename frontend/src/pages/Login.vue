<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-title">📜 家谱管理系统</div>
      <div class="login-sub">家族谱系数字化管理</div>

      <a-form layout="vertical" autocomplete="off" :model="form" @finish="handleLogin">
        <a-form-item label="用户名" name="username" :rules="[{ required: true, message: '请输入用户名' }]">
          <a-input v-model:value="form.username" size="large" placeholder="用户名" autocomplete="off" />
        </a-form-item>
        <a-form-item label="密码" name="password" :rules="[{ required: true, message: '请输入密码' }]">
          <a-input-password
            v-model:value="form.password"
            size="large"
            placeholder="密码"
            autocomplete="off"
          />
        </a-form-item>

        <!-- 可见动态码：打开页面即自动生成并展示，抄入下方输入框后点「登录」/回车一次提交 -->
        <div
          v-if="issuedCode && codeTtl > 0"
          class="hotp-card"
          :class="{ 'hotp-card--expiring': codeTtl <= 10 }"
          title="点击自动填入下方输入框"
          @click="fillCode"
        >
          <span class="hotp-key">🔐 动态码</span>
          <span class="hotp-code">{{ issuedCode }}</span>
          <span class="hotp-ttl">剩 {{ codeTtl }} 秒 · 点击填入</span>
        </div>
        <a-form-item label="动态验证码">
          <a-input
            ref="codeInputRef"
            v-model:value="form.code"
            size="large"
            maxlength="6"
            placeholder="请输入上方展示的 6 位动态码"
            autocomplete="off"
            :disabled="!issuedCode || codeTtl <= 0"
            @input="onCodeInput"
          />
          <div v-if="codeLoading || !issuedCode || codeTtl <= 0" class="code-hint">
            <template v-if="codeLoading">⏳ 正在生成本次动态码…</template>
            <template v-else>动态码加载失败，点「登录」会自动重新生成（60 秒有效、一次性使用）</template>
          </div>
        </a-form-item>

        <a-form-item style="margin-bottom: 12px">
          <a-checkbox v-model:checked="form.remember">记住我（1 天内免登录）</a-checkbox>
          <div class="tip">未勾选时，关闭浏览器即自动退出登录</div>
        </a-form-item>
        <a-button type="primary" html-type="submit" size="large" block :loading="submitting || codeLoading">
          {{ submitting ? '登录中…' : codeLoading ? '生成动态码中…' : '登 录' }}
        </a-button>
        <div v-if="pageError" class="login-error">{{ pageError }}</div>
      </a-form>
      <AppCopyright />
      <!-- 安装证书入口：浏览器无法检测客户端是否已装证书，故放角落作可选项，不再用横幅强调 -->
      <a
        v-if="showCertWarn"
        class="cert-corner"
        :href="certToolUrl"
        :download="certToolDownloadName"
        :title="'下载证书安装工具（自动识别系统为 ' + certToolLabel + '）'"
      >{{ certToolLabel }}</a>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { hotpCodeApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

// ---------- 证书安装工具下载（nginx /cert/ 提供 octet-stream 下载） ----------
const isWindows =
  typeof navigator !== 'undefined' &&
  /win/i.test((navigator as unknown as { platform?: string }).platform || navigator.userAgent)
const certWinUrl = '/cert/cert-tool-windows.exe'
const certLinuxUrl = '/cert/cert-tool-linux'
const certToolUrl = isWindows ? certWinUrl : certLinuxUrl
// 按当前系统自动识别（Windows → .exe，其余按 Linux 处理），按钮直接显示对应版本
const certToolLabel = isWindows ? '安装证书（Windows）' : '安装证书（Linux）'
// 下载后的文件名（同域 download 属性生效）：统一用系统名，不再暴露 cert-tool-*/yugsight
const certToolDownloadName = isWindows
  ? '家谱管理系统证书管理工具.exe'
  : '家谱管理系统证书管理工具'
// 仅 HTTPS 部署（自签证书场景）显示提示；开发环境 http 不显示
const showCertWarn =
  typeof window !== 'undefined' && window.location.protocol === 'https:'

const form = reactive({ username: '', password: '', remember: false, code: '' })
const submitting = ref(false)
const codeLoading = ref(false)
const pageError = ref('')
/** 服务端签发的本次动态码（展示给用户抄录） */
const issuedCode = ref('')
/** 剩余有效秒数 */
const codeTtl = ref(0)
const codeInputRef = ref()
let codeTimer: number | undefined
/**
 * sessionStorage 记住已签发码与到期时间：页面刷新/误关后 60s 内沿用同一码，
 * 到期或登录失败等被作废后才重新取码（码本就是明文展示，暂存不增加泄露面）。
 */
const HOTP_KEY = 'login_hotp'
let hotpExpAt = 0
/** 剩余有效期按「服务端签发时刻 + 有效期」折算到本地时钟，remainMs 即折算后的剩余毫秒 */
function saveHotp(code: string, remainMs: number) {
  hotpExpAt = Date.now() + remainMs
  try {
    sessionStorage.setItem(HOTP_KEY, JSON.stringify({ code, expAt: hotpExpAt }))
  } catch {
    /* 隐私模式等写入失败可忽略：本次会话内仍可用 */
  }
}
function clearSavedHotp() {
  hotpExpAt = 0
  try {
    sessionStorage.removeItem(HOTP_KEY)
  } catch {
    /* ignore */
  }
}
function startTtlTimer() {
  clearCodeTimer()
  tickTtl()
  codeTimer = window.setInterval(tickTtl, 1000)
}

/** 单次倒计时推进：页面从后台切回/休眠唤醒时也调用，避免沿用已过期的旧码 */
function tickTtl() {
  if (!hotpExpAt) return
  const remain = Math.max(0, Math.round((hotpExpAt - Date.now()) / 1000))
  codeTtl.value = remain
  if (remain > 0) return
  clearCodeTimer()
  issuedCode.value = ''
  // 用户已经抄了码才提示：无人值守时的自动续期不必反复弹提示干扰
  const hadInput = form.code.length > 0
  form.code = ''
  if (hadInput) message.info('动态码已过期，已自动生成新码，请重新填写')
  void getCode(true)
}
/** 刷新页面后沿用此前签发的码（未过期即复用，不再向服务端重复取码） */
function restoreHotp(): boolean {
  try {
    const raw = sessionStorage.getItem(HOTP_KEY)
    if (!raw) return false
    const saved = JSON.parse(raw) as { code?: string; expAt?: number }
    if (
      !saved ||
      typeof saved.code !== 'string' ||
      !/^\d{6}$/.test(saved.code) ||
      typeof saved.expAt !== 'number' ||
      saved.expAt <= Date.now()
    ) {
      clearSavedHotp()
      return false
    }
    // 剩余不足 30 秒就直接用新码：否则刚刷新就过期，用户还是来不及抄
    if (saved.expAt - Date.now() < 30_000) {
      clearSavedHotp()
      return false
    }
    hotpExpAt = saved.expAt
    issuedCode.value = saved.code
    form.code = ''
    startTtlTimer()
    return true
  } catch {
    clearSavedHotp()
    return false
  }
}

function clearCodeTimer() {
  if (codeTimer !== undefined) {
    clearInterval(codeTimer)
    codeTimer = undefined
  }
}

function onCodeInput() {
  form.code = form.code.replace(/\D/g, '').slice(0, 6)
}

/** 点击动态码卡片一键填入：仍需人工点击（防脚本），但免去手抄错/抄到已换掉的旧码 */
function fillCode() {
  if (!issuedCode.value || codeTtl.value <= 0) return
  form.code = issuedCode.value
  pageError.value = ''
  ;(codeInputRef.value as unknown as { focus?: () => void })?.focus?.()
}

/** 作废当前码（登录失败 / 过期），下次点击登录自动重新签发 */
function invalidateCode() {
  clearCodeTimer()
  clearSavedHotp()
  issuedCode.value = ''
  codeTtl.value = 0
  form.code = ''
}

/** 向服务端取 6 位动态码并展示，附带 60s 倒计时；过期自动重新签发（与用户名无关） */
async function getCode(autoRenew = false): Promise<boolean> {
  clearCodeTimer()
  codeLoading.value = true
  try {
    const res = await hotpCodeApi()
    const ttl = res.expires_in || 60
    // 按服务端签发的绝对过期时间折算到本地时钟，并预留 1 秒安全余量：
    // 网络慢/后端繁忙时不再出现「前端还剩几秒、服务端其实已过期」——
    // 那会让用户明明抄对了码却登录失败（旧实现从收到响应才开始计时）。
    const serverExpAt = (res.server_time || Date.now()) + ttl * 1000
    const remainMs = Math.max(1000, serverExpAt - Date.now() - 1000)
    issuedCode.value = res.code
    form.code = ''
    saveHotp(res.code, remainMs)
    startTtlTimer()
    return true
  } catch {
    message.error(autoRenew ? '自动续码失败，请点「登录」重试' : '获取动态码失败，请重试')
    return false
  } finally {
    codeLoading.value = false
  }
}

async function handleLogin() {
  if (submitting.value || codeLoading.value) return
  pageError.value = ''
  if (!form.username.trim()) {
    message.warning('请输入用户名')
    return
  }
  if (!form.password) {
    message.warning('请输入密码')
    return
  }
  // 兜底：动态码尚未就绪时先取码（正常打开页面即已自动生成并展示）
  if (!issuedCode.value || codeTtl.value <= 0) {
    await getCode()
    return
  }
  if (!/^\d{6}$/.test(form.code)) {
    message.warning('请将上方展示的动态码抄入输入框（6 位数字）')
    ;(codeInputRef.value as unknown as { focus?: () => void })?.focus?.()
    return
  }
  // 填的是已被换掉的旧码（期间自动续期过）时，直接说清并换成新码，
  // 不再发一次必然失败的请求、也不再让用户看到含糊的「动态码校验失败」
  if (form.code !== issuedCode.value) {
    pageError.value = `动态码已刷新，请填写当前显示的 6 位码：${issuedCode.value}`
    form.code = ''
    ;(codeInputRef.value as unknown as { focus?: () => void })?.focus?.()
    return
  }
  submitting.value = true
  try {
    await auth.login(form.username.trim(), form.password, form.remember, form.code)
    // 码已被服务端消费：清除暂存，避免下次退出重登时沿用已作废的码
    clearSavedHotp()
    const redirect = (route.query.redirect as string) || '/tree3d'
    router.push(redirect)
  } catch (err) {
    // 码已消费/错码/密码错误等：作废本次码
    invalidateCode()
    // 静默补发一张新码并展示，用户修正后可直接再次提交，无需先点登录取码
    void getCode(true)
    const e = err as { response?: { status?: number; data?: { detail?: string } } }
    pageError.value = e?.response?.data?.detail || '登录失败，请重试'
  } finally {
    submitting.value = false
  }
}

// 打开页面即自动取码展示：无需先输用户名密码，抄入动态码后一次提交即可登录
// 若此前取的码仍在 60s 有效期内（刷新页面/误操作返回），沿用同一码，到期才重新取
/** 切回标签页/休眠唤醒后立即校准倒计时，避免继续沿用已过期却被「冻结」的旧码 */
function onVisibilityChange() {
  if (document.visibilityState === 'visible') tickTtl()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  if (!restoreHotp()) void getCode(false)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  clearCodeTimer()
})
</script>

<style scoped>
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1d3b5c 0%, #2b5876 100%);
  padding: 20px;
  box-sizing: border-box;
}
.login-card {
  width: 440px;
  background: #fff;
  border-radius: 10px;
  padding: 40px 36px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.25);
}
.login-title {
  font-size: 24px;
  font-weight: 700;
  text-align: center;
  margin-bottom: 6px;
}
.login-sub {
  text-align: center;
  color: #888;
  margin-bottom: 16px;
  font-size: 13px;
}
.cert-corner {
  display: block;
  text-align: center;
  margin-top: 10px;
  font-size: 12px;
  color: #b7b7b7;
  text-decoration: none;
  transition: color 0.2s;
}
.cert-corner:hover {
  color: #1677ff;
}
.hotp-card {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  border: 1px dashed #1677ff;
  border-radius: 8px;
  background: #eef4ff;
  padding: 6px 12px;
  margin-bottom: 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}
.hotp-card:hover {
  background: #e0ebff;
}
/* 剩余 10 秒内高亮：提醒用户尽快抄录，避免刚填完就被自动续期换掉 */
.hotp-card--expiring {
  border-color: #fa8c16;
  background: #fff7e6;
}
.hotp-card--expiring .hotp-ttl {
  color: #d46b08;
  font-weight: 600;
}
.hotp-key {
  font-size: 13px;
  color: #0958d9;
  white-space: nowrap;
}
.hotp-code {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 4px;
  color: #003eb3;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  line-height: 1.4;
}
.hotp-ttl {
  font-size: 12px;
  color: #888;
  white-space: nowrap;
}
.code-hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
  line-height: 1.5;
}
.tip {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}
.login-error {
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: #cf1322;
}
</style>
