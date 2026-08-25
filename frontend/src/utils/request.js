import { message } from 'antd'

const BASE_URL = '/api'

// --- 令牌刷新（access 短命 + refresh 轮换）---

/** 解析 JWT payload 里的 exp（秒）；解析失败返回 0 */
function parseJwtExp(token) {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp || 0
  } catch {
    return 0
  }
}

function clearAuthStorage() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('refreshToken')
}

function redirectToLogin() {
  clearAuthStorage()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

// 单飞：并发请求同时 401 时只发一次刷新
let refreshPromise = null

/** 用 refreshToken 换新令牌。成功存储并返回 true，失败清本地返回 false。 */
function doRefresh() {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    const refreshToken = localStorage.getItem('refreshToken')
    if (!refreshToken) return false
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken }),
      })
      if (!res.ok) {
        clearAuthStorage()
        return false
      }
      const data = await res.json()
      localStorage.setItem('token', data.data.token)
      localStorage.setItem('refreshToken', data.data.refreshToken)
      return true
    } catch {
      return false
    }
  })().finally(() => { refreshPromise = null })
  return refreshPromise
}

/**
 * 返回一个可用的 access token：若已过期/临近过期（<60s）则先刷新。
 * 供无法反应式重试的流式/下载请求在发起前调用。刷新失败会跳登录页并返回 null。
 */
export async function getValidToken() {
  const token = localStorage.getItem('token')
  if (!token) return null
  const exp = parseJwtExp(token)
  const now = Math.floor(Date.now() / 1000)
  if (exp && exp - now < 60) {
    const ok = await doRefresh()
    if (!ok) { redirectToLogin(); return null }
    return localStorage.getItem('token')
  }
  return token
}

async function request(url, options = {}, _retried = false) {
  const token = localStorage.getItem('token')

  const config = {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  }

  if (config.body && typeof config.body === 'object') {
    config.body = JSON.stringify(config.body)
  }

  const res = await fetch(`${BASE_URL}${url}`, config)

  // 401 → 登录/刷新请求本身直接走错误处理，不触发刷新
  if (res.status === 401) {
    const isAuthFlow = url === '/auth/login' || url === '/auth/refresh'
    if (!isAuthFlow) {
      // 用 refresh token 静默刷新后重试一次
      if (!_retried) {
        const ok = await doRefresh()
        if (ok) return request(url, options, true)
      }
      redirectToLogin()
      return Promise.reject(new Error('未登录或登录已过期'))
    }
  }

  // 403
  if (res.status === 403) {
    if (!options.silent) message.error('无权限执行此操作')
    return Promise.reject(new Error('无权限'))
  }

  const data = await res.json()

  if (!res.ok) {
    let errMsg = data?.error?.message
    // Mock 系列后端返回的是 {"error": "路由已锁定…"}，error 是字符串而非对象，
    // 不认这一种就只能显示「请求失败 (423)」，用户看不到被拦的原因
    if (!errMsg && typeof data?.error === 'string') errMsg = data.error
    if (!errMsg && typeof data?.detail === 'string') errMsg = data.detail
    // Pydantic 422 验证错误: detail 是数组
    if (!errMsg && Array.isArray(data?.detail)) {
      const fieldErrors = data.detail.map(d => {
        const field = d.loc?.[d.loc.length - 1] || ''
        return `${field}: ${d.msg}`
      })
      errMsg = fieldErrors.join('；')
    }
    errMsg = errMsg || `请求失败 (${res.status})`
    // silent：常驻轮询用（顶栏服务状态等），失败别每隔几十秒弹一次 toast 刷屏
    if (!options.silent) message.error(errMsg)
    // 业务错误码要带上。有些 409 不是"失败"而是"需要你确认一下"
    // （模块合并会改用例归属，后端先回 FOLDER_MERGE_REQUIRED + 会搬几条），
    // 调用方光有一句 message 没法分辨该弹确认框还是该报错。
    const err = new Error(errMsg)
    err.code = data?.error?.code
    err.status = res.status
    return Promise.reject(err)
  }

  return data
}

export const api = {
  get: (url, options) => request(url, options),
  post: (url, body) => request(url, { method: 'POST', body }),
  put: (url, body) => request(url, { method: 'PUT', body }),
  patch: (url, body, options) => request(url, { method: 'PATCH', body, ...options }),
  del: (url) => request(url, { method: 'DELETE' }),
  delete: (url) => request(url, { method: 'DELETE' }),
  download: async (url) => {
    const token = await getValidToken()
    const res = await fetch(`${BASE_URL}${url}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) throw new Error(`下载失败 (${res.status})`)
    return res.blob()
  },
  /**
   * SSE 流式请求 — 用于 AI 生成接口
   * @param {string} url
   * @param {object} body
   * @param {{ onChunk?: (data: object) => void, onDone?: (data: object) => void, onError?: (msg: string) => void }} callbacks
   * @returns {{ abort: () => void }}
   */
  stream: (url, body, { onChunk, onDone, onError } = {}) => {
    const controller = new AbortController()

    ;(async () => {
      try {
        const token = await getValidToken()
        const res = await fetch(`${BASE_URL}${url}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        })

        if (!res.ok) {
          const text = await res.text()
          onError?.(text || `请求失败 (${res.status})`)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          const lines = buffer.split('\n\n')
          buffer = lines.pop()

          for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed || !trimmed.startsWith('data: ')) continue
            const payload = trimmed.slice(6)
            if (payload === '[DONE]') { onDone?.({}); return }
            try {
              const data = JSON.parse(payload)
              if (data.type === 'error') {
                onError?.(data.message || '生成失败')
                return
              }
              if (data.type === 'done') {
                onDone?.(data)
              } else {
                onChunk?.(data)
              }
            } catch { /* skip unparseable chunks */ }
          }
        }
      } catch (err) {
        if (err.name !== 'AbortError') {
          onError?.(err.message || '网络错误')
        }
      }
    })()

    return { abort: () => controller.abort() }
  },

  /**
   * SSE GET 事件流 — 用于场景生成任务进度（ADR-3 回放契约）
   * 支持 afterSeq 断线续传：断线后自动以最后 seq 重连。
   *
   * @param {string} url   端点路径（不含 BASE_URL）
   * @param {{ afterSeq?: number, onEvent?: (data: object) => void, onEnd?: (data: object) => void,
   *           onError?: (msg: string) => void, reconnectMs?: number, maxRetries?: number }} opts
   * @returns {{ abort: () => void }}
   */
  sseStream: (url, { afterSeq = 0, onEvent, onEnd, onError, reconnectMs = 3000, maxRetries = 30 } = {}) => {
    let controller = new AbortController()
    let cursor = afterSeq
    let retries = 0
    let stopped = false

    const connect = async () => {
      if (stopped) return
      controller = new AbortController()
      const token = await getValidToken()
      const sep = url.includes('?') ? '&' : '?'
      const fullUrl = `${BASE_URL}${url}${sep}afterSeq=${cursor}`
      try {
        const res = await fetch(fullUrl, {
          headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          signal: controller.signal,
        })
        if (!res.ok) {
          onError?.(`SSE 连接失败 (${res.status})`)
          return
        }
        retries = 0
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n\n')
          buffer = lines.pop()

          for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed || trimmed.startsWith(':')) continue  // 心跳 ping
            if (!trimmed.startsWith('data: ')) continue
            try {
              const data = JSON.parse(trimmed.slice(6))
              if (data.seq) cursor = data.seq
              if (data.type === 'stream_end') {
                onEnd?.(data)
                stopped = true
                return
              }
              onEvent?.(data)
            } catch { /* skip */ }
          }
        }
        // 流正常关闭但未收到 stream_end → 重连
        if (!stopped) scheduleReconnect()
      } catch (err) {
        if (err.name === 'AbortError') return
        if (!stopped) scheduleReconnect()
      }
    }

    const scheduleReconnect = () => {
      if (stopped || retries >= maxRetries) {
        onError?.('SSE 重连次数超限')
        return
      }
      retries++
      setTimeout(connect, reconnectMs)
    }

    connect()

    return {
      abort: () => {
        stopped = true
        controller.abort()
      },
    }
  },
}
