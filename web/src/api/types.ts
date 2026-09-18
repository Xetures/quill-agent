/**
 * 后端接口的数据形状。
 *
 * 这些类型和 `src/quill_agent/` 里的 pydantic 模型一一对应 —— 后端改了字段，
 * 这里同步改，TypeScript 会在所有用到的地方立刻报错。这也是用 TS 的收益：
 * 换前端最容易出的错就是「字段名对不上」，而那种错运行时才发现得了。
 */

export interface ConversationBrief {
  id: string
  title: string
  /** Unix 时间戳（秒）。展示成什么格式由前端决定。 */
  updated_at: number
}

export interface ToolStep {
  name: string
  arguments: string
  result: string
  /**
   * 这次工具调用花了多久（秒）。
   *
   * 标成可选是如实反映现实：`elapsed` 是后加的字段，在这之前产生的会话记录里
   * 没有它。类型写成必填的话，运行时拿到 `undefined` 就会在下游炸掉 ——
   * 而 TS 什么都不会提示。
   */
  elapsed?: number
}

export interface RunStats {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  /** 整轮耗时（秒），含工具执行。 */
  elapsed: number
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  ts?: string
  /**
   * 用户消息带过的附件名。
   *
   * 只记名字不记内容：附件已经落到工作目录，模型拿到的是那里的路径。
   * 这个字段纯粹是给界面回看用的 —— 不记的话，界面上完全看不出这条消息
   * 带过附件，而模型明明看得见。
   */
  files?: string[]
  steps?: ToolStep[]
  /** 系统提示（不是模型输出），界面上标黄展示。 */
  notices?: string[]
  /** 推理模型的思维链，只用于回看。 */
  reasoning?: string
  stats?: RunStats
}

export interface ModelConfig {
  id: string
  name: string
  base_url: string
  protocol: 'openai' | 'anthropic'
  api_key: string
  models: string[]
}

/** 「连接 + 模型名」摊平后的下拉选项，`key` 就是后端的稳定标识。 */
export interface ModelOption {
  key: string
  config_id: string
  config_name: string
  model: string
  label: string
}

export interface PromptMode {
  id: string
  name: string
  /** 类别 -> 提示词名。 */
  settings: Record<string, string>
  preferred_model: string
}

export interface ToolSpec {
  name: string
  description: string
  category: string
  parameters: Record<string, unknown>
  kind: string
  enabled: boolean
}

export interface SkillItem {
  name: string
  description: string
  enabled: boolean
}

export interface MemoryItem {
  id: string
  text: string
  enabled: boolean
  created_at: string
}

/** `GET /workdir` 的响应。`native_picker` 表示后端所在环境能否拉系统对话框。 */
export interface WorkDirInfo {
  path: string
  native_picker: boolean
}

/** `GET /workdir/browse` 的响应：某个目录下的子目录列表。 */
export interface BrowseResult {
  path: string
  parent: string
  dirs: { name: string; path: string }[]
  error: string | null
}

/** `POST /workdir/pick` 的响应。`path` 为 null 表示用户取消了。 */
export interface PickResult {
  /** 环境是否支持系统目录选择器。false 时界面应改用浏览器里的目录浏览。 */
  available: boolean
  path: string | null
  error: string | null
}

/** 一轮对话里 SSE 推回来的事件。 */
export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'tool'; step: ToolStep }
  | { type: 'notice'; text: string }
  | { type: 'done'; message: Message }
