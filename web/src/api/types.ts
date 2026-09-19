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
  /** 上下文窗口大小（tokens），任务页用量仪表盘的分母。 */
  context_window: number
}

/** 「连接 + 模型名」摊平后的下拉选项，`key` 就是后端的稳定标识。 */
export interface ModelOption {
  key: string
  config_id: string
  config_name: string
  model: string
  label: string
  /** 上下文窗口大小（tokens），跟着连接配置走。 */
  context_window: number
}

/**
 * 提示词组：从六类提示词里各挑一个（可以不挑）拼成的一套提示词。
 *
 * 原先这个东西就叫「模式」，现在模式指四类组的组合，它只是其中提示词那一类。
 * 偏好模型不在这里 —— 搬到模式上了（见 `Mode`）。
 */
export interface PromptGroup {
  id: string
  name: string
  /** 功能简介：和工具组、技能组一致，给模式编辑界面用。 */
  description: string
  /** 类别 -> 提示词名。允许为空 —— 那就是不带任何系统提示词的纯问答。 */
  settings: Record<string, string>
}

/**
 * 模式：Agent 的一套完整配置 = 三类组 + 记忆开关 + 偏好模型。
 *
 * 组按 **id** 引用（不是名字）：组改名后模式不会跟着失效。
 * 空串表示这一类什么都不给 —— 三个都空就是纯问答模式。
 */
export interface Mode {
  id: string
  name: string
  description: string
  prompt_group_id: string
  tool_group_id: string
  skill_group_id: string
  memory_enabled: boolean
  /** 偏好模型的稳定标识（"连接id::模型名"）；空表示沿用任务页当前模型。 */
  preferred_model: string
}

/**
 * `GET /modes` 的返回：模式列表，外加三类组 id -> 名字的对照表。
 *
 * 模式表里要显示「用了哪个组」，让前端自己再拉三份组列表既慢又容易对不上，
 * 所以后端一并给出来。
 */
export interface ModeList {
  modes: Mode[]
  groups: {
    prompt: Record<string, string>
    tool: Record<string, string>
    skill: Record<string, string>
  }
}

/** 提示词库：六类，以及每类下可选哪些提示词（只有名字，正文要单独取）。 */
export interface PromptLib {
  categories: string[]
  names: Record<string, string[]>
}

export interface ToolSpec {
  name: string
  description: string
  category: string
  parameters: Record<string, unknown>
  kind: string
}

/**
 * 工具组：给模型的工具搭配方案。
 *
 * 模式将变成四类组（提示词 / 工具 / 技能 / 记忆）的组合，这是其中工具这一环。
 * `tools` 允许为空 —— 「纯对话」组要的就是一个工具都不给。
 */
export interface ToolGroup {
  id: string
  name: string
  description: string
  tools: string[]
}

export interface SkillItem {
  name: string
  description: string
}

/**
 * 技能组：给模型的技能搭配方案。
 *
 * 与工具组同构（模式将变成四类组的组合）；`skills` 允许为空 ——
 * 有些模式一个技能都不该给。
 */
export interface SkillGroup {
  id: string
  name: string
  description: string
  skills: string[]
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
