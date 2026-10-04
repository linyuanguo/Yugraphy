/** 登录态存储：默认会话级（关闭浏览器即失效），勾选「记住我」才持久化（带过期时间）。 */
const TOKEN_KEY = 'token'
const USER_KEY = 'user'
const EXP_KEY = 'token_exp'
const LOGIN_AT_KEY = 'login_at'

const DEFAULT_REMEMBER_MS = 1 * 24 * 60 * 60 * 1000

function clearPersisted() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  localStorage.removeItem(EXP_KEY)
  localStorage.removeItem(LOGIN_AT_KEY)
}

function clearSession() {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
  sessionStorage.removeItem(LOGIN_AT_KEY)
}

/** 取当前有效 token；持久化的那份过期会自动清除 */
export function getToken(): string {
  const s = sessionStorage.getItem(TOKEN_KEY)
  if (s) return s

  const persisted = localStorage.getItem(TOKEN_KEY)
  if (!persisted) return ''
  const exp = Number(localStorage.getItem(EXP_KEY) || 0)
  if (exp && Date.now() < exp) return persisted

  // 「记住我」已过期
  clearPersisted()
  return ''
}

/** 是否为「记住我」的持久登录（用于界面提示） */
export function isRemembered(): boolean {
  return !sessionStorage.getItem(TOKEN_KEY) && !!localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string, remember: boolean, expiresInSec = 0) {
  if (remember) {
    localStorage.setItem(TOKEN_KEY, token)
    const ms = expiresInSec > 0 ? expiresInSec * 1000 : DEFAULT_REMEMBER_MS
    localStorage.setItem(EXP_KEY, String(Date.now() + ms))
    sessionStorage.removeItem(TOKEN_KEY)
  } else {
    // 会话级：仅当前标签页有效，关闭浏览器即失效
    clearPersisted()
    sessionStorage.setItem(TOKEN_KEY, token)
  }
}

export function getUser<T>(): T | null {
  const raw = sessionStorage.getItem(USER_KEY) || localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/** 记录本次登录时间（与 token 同样遵循「记住我」/会话级存储） */
export function setLoginAt(ts: number, remember: boolean) {
  if (remember) {
    localStorage.setItem(LOGIN_AT_KEY, String(ts))
    sessionStorage.removeItem(LOGIN_AT_KEY)
  } else {
    sessionStorage.setItem(LOGIN_AT_KEY, String(ts))
  }
}

export function getLoginAt(): number | null {
  const raw = sessionStorage.getItem(LOGIN_AT_KEY) || localStorage.getItem(LOGIN_AT_KEY)
  if (!raw) return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

export function setUser(user: unknown, remember: boolean) {
  const raw = JSON.stringify(user)
  if (remember) {
    localStorage.setItem(USER_KEY, raw)
    sessionStorage.removeItem(USER_KEY)
  } else {
    localStorage.removeItem(USER_KEY)
    sessionStorage.setItem(USER_KEY, raw)
  }
}

/** 彻底登出：会话级与持久化的登录态一并清除 */
export function clearAuth() {
  clearSession()
  clearPersisted()
}
