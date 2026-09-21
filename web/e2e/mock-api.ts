import type { Page } from '@playwright/test'

/**
 * 把 `/api/*` 全部拦掉，返回一份最小可用的假数据。
 *
 * 为什么全拦而不是起真后端：e2e 要验的是前端（界面 + SSE 解析 + 渲染）。
 * 全拦之后测试不依赖后端、不依赖模型，跑得快也稳。
 */

export const CONVERSATION_ID = 'c-e2e-1'

export const brief = {
  id: CONVERSATION_ID,
  title: '测试会话',
  updated_at: 1758400000,
}

export const model = {
  key: 'cfg-1::m-1',
  config_id: 'cfg-1',
  config_name: '测试连接',
  model: 'm-1',
  label: '测试连接 / m-1',
  context_window: 128000,
}

export const mode = {
  id: 'mode-1',
  name: '测试模式',
  description: '',
  prompt_group_id: '',
  tool_group_id: '',
  skill_group_id: '',
  memory_enabled: false,
  preferred_model: '',
}

/** 一个 SSE 事件块。 */
export function sse(name: string, data: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
}

/** `done` 事件的载荷（一条已落盘的助手消息）。 */
export function doneMessage(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { role: 'assistant', content: '', steps: [], notices: [], ...overrides }
}

/** `/api/chat` 要返回什么。可以是异步的 —— 用来模拟「还在跑」的中间态。 */
export type ChatResponse = () => Promise<{ status: number; body: string }> | { status: number; body: string }

export interface MockOptions {
  /** `/api/chat` 的响应；不给就返回一个空流。 */
  chat?: ChatResponse
}

export async function mockApi(page: Page, options: MockOptions = {}): Promise<void> {
  // 只匹配「路径以 /api/ 开头」的请求。**不能用 glob `**/api/**`** —— 它会把前端自己的
  // 模块请求也拦下来（`/src/api/chat.ts` 的 URL 里就含 `/api/`），模块拿到一段 JSON、
  // 应用直接挂不起来，页面一片空白，而报错只是一句难懂的 MIME type 警告
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/preferences') return route.fulfill({ json: {} })
    if (path === '/api/conversations') {
      return route.fulfill({ json: { active: [brief], archived: [] } })
    }
    if (path === `/api/conversations/${CONVERSATION_ID}`) {
      return route.fulfill({ json: { messages: [] } })
    }
    if (path === '/api/models') return route.fulfill({ json: { options: [model] } })
    if (path === '/api/modes') {
      return route.fulfill({ json: { modes: [mode], groups: { prompt: {}, tool: {}, skill: {} } } })
    }
    if (path === '/api/workdir') {
      return route.fulfill({ json: { path: '/tmp/quill-e2e', native_picker: false } })
    }

    if (path === '/api/chat' && route.request().method() === 'POST') {
      const res = await (options.chat?.() ?? { status: 200, body: '' })
      // 非 2xx 时不能还报 event-stream，否则前端会当成正常流去解析
      const ok = res.status >= 200 && res.status < 300
      return route.fulfill({
        status: res.status,
        contentType: ok ? 'text/event-stream' : 'text/plain',
        body: res.body,
      })
    }

    // 其余端点（工具 / 技能 / 记忆…）给个空对象，别让请求真打出去
    return route.fulfill({ json: {} })
  })
}
