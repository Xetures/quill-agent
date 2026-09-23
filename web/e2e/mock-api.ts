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

/** 任务页读 `options`（摊平后的下拉项）。 */
export const modelOption = {
  key: 'cfg-1::m-1',
  config_id: 'cfg-1',
  config_name: '测试连接',
  model: 'm-1',
  label: '测试连接 / m-1',
  context_window: 128000,
}

/** API 设置页读 `configs`（原始连接）。同一个端点两种口径，后端一份响应里都给。 */
export const modelConfig = {
  id: 'cfg-1',
  name: '测试连接',
  base_url: 'https://example.test',
  protocol: 'openai',
  api_key: 'sk-test',
  models: ['m-1'],
  context_windows: { 'm-1': 128000 },
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
export type ChatResponse = () =>
  | Promise<{ status: number; body: string }>
  | { status: number; body: string }

export interface MockOptions {
  /** `/api/chat` 的响应；不给就返回一个空流。 */
  chat?: ChatResponse
}

export async function mockApi(page: Page, options: MockOptions = {}): Promise<void> {
  // 只匹配「路径以 /api/ 开头」的请求。**不能用 glob `**/api/**`** —— 它会把前端自己的
  // 模块请求也拦下来（`/src/api/chat.ts` 的 URL 里就含 `/api/`），模块拿到一段 JSON、
  // 应用直接挂不起来，而报错只是一句难懂的 MIME type 警告
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/preferences') return route.fulfill({ json: {} })
    if (path === '/api/health') return route.fulfill({ json: { version: '0.1.4' } })

    if (path === '/api/conversations') {
      return route.fulfill({ json: { active: [brief], archived: [] } })
    }
    if (path === `/api/conversations/${CONVERSATION_ID}`) {
      return route.fulfill({ json: { messages: [] } })
    }

    // 同一个端点被两个页面用两种口径读，所以两种字段都要给
    if (path === '/api/models') {
      return route.fulfill({ json: { options: [modelOption], configs: [modelConfig] } })
    }
    // MCP 服务器：MCP 页与工具组弹窗都要。**同样必须给**（理由见下面 providers 那条）
    if (path === '/api/mcp/servers') {
      return route.fulfill({ json: { servers: [] } })
    }
    // 服务商清单：模型页的「服务商」下拉从这儿取。**必须给**：不给的话这个字段是
    // undefined，而页面里到处在 `.length` 它 —— 弹窗一渲染就抛
    // 「Cannot read properties of undefined (reading 'length')」，
    // 表现是「点了新建连接没反应」（真实前端和后端都不会这样，是 mock 的缺口）
    if (path === '/api/providers') {
      return route.fulfill({ json: { providers: [] } })
    }
    if (path === '/api/protocols') {
      return route.fulfill({
        json: {
          protocols: [
            {
              value: 'openai',
              label: 'OpenAI Chat Completions',
              default_base_url: 'https://api.openai.com/v1',
              hint: 'sk-...',
            },
          ],
        },
      })
    }

    if (path === '/api/modes') {
      return route.fulfill({ json: { modes: [mode], groups: { prompt: {}, tool: {}, skill: {} } } })
    }
    if (path === '/api/prompt-groups') return route.fulfill({ json: { groups: [] } })
    if (path === '/api/prompts') return route.fulfill({ json: { categories: [], items: [] } })
    if (path === '/api/tool-groups') return route.fulfill({ json: { groups: [] } })
    if (path === '/api/tools') return route.fulfill({ json: { tools: [], categories: [] } })
    if (path === '/api/skill-groups') return route.fulfill({ json: { groups: [] } })
    if (path === '/api/skills') return route.fulfill({ json: { skills: [], dir: '/tmp/skills' } })
    if (path === '/api/memory') return route.fulfill({ json: { items: [], max: 50 } })
    if (path === '/api/usage') {
      return route.fulfill({ json: { dates: [], series: [], tasks: [] } })
    }
    if (path === '/api/search') {
      return route.fulfill({
        json: {
          config: { backend: 'tavily', keys: {}, base_url: '', max_results: 5 },
          backends: [
            {
              value: 'tavily',
              label: 'Tavily',
              endpoint: 'https://api.tavily.com',
              hint: 'tvly-...',
            },
          ],
          limits: { min: 1, max: 10, default: 5 },
        },
      })
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

    // 其余端点给个空对象，别让请求真打出去
    return route.fulfill({ json: {} })
  })
}
