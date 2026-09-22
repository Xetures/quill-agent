/**
 * 后端接口的薄封装。
 *
 * 只做两件事：拼 URL、把后端的错误信息翻成异常。不引 axios —— 原生 fetch
 * 够用，少一个依赖就少一份要跟着升级的东西。
 */

const BASE = '/api'

function authHeaders(): HeadersInit {
  const token = new URLSearchParams(window.location.search).get('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * 后端返回了非 2xx 时抛的异常。
 *
 * 不导出：目前所有调用方都只用 `message`（经 `utils/error.ts` 转成文案），
 * 没有一处会去 `instanceof` 判断。等真的需要按状态码分支（比如 401 跳登录）时
 * 再加上 export 也不迟。
 */
class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers ?? {}) },
    ...init,
  })

  if (!response.ok) {
    // FastAPI 的错误体是 { detail: "..." }，把它取出来当异常消息 ——
    // 这样界面上显示的是后端真正想说的话（比如「API Key 与协议格式不符」），
    // 而不是一句没用的「请求失败 400」
    let detail = `请求失败（${response.status}）`
    try {
      const body: unknown = await response.json()
      if (isDetail(body)) detail = body.detail
    } catch {
      // 响应不是 JSON，沿用默认文案
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}

function isDetail(value: unknown): value is { detail: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    'detail' in value &&
    typeof (value as { detail: unknown }).detail === 'string'
  )
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
