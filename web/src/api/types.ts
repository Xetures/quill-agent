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
  /**
   * 最后一次请求的输入量 = 这一轮结束时上下文实际有多大。
   *
   * 和 `prompt_tokens` 不是一个口径：后者是一轮里多次请求的累加（每轮都要重发
   * 同一份上下文），值随工具轮次多少虚高几倍到十几倍。仪表盘要的是窗口占用，
   * 所以读这个。
   *
   * 标成可选是因为它是后加的字段，在这之前落盘的记录里没有（见下面的 `elapsed`，
   * 同一个理由）。
   */
  context_tokens?: number
  /** 整轮耗时（秒），含工具执行。 */
  elapsed: number
}

/** 任务清单里一项的状态；和后端 `tools/todo.py` 的 STATUSES 一一对应。 */
export type TodoStatus = 'pending' | 'in_progress' | 'completed'

/**
 * 任务清单里的一项。
 *
 * 由 `todo_write` 工具产生。清单是**全量替换**的 —— 后端的每一次提交都带着完整条目，
 * 前端不用做增量合并（那正是「每次都提交完整清单」换来的好处）。
 */
export interface TodoItem {
  content: string
  status: TodoStatus
}

export interface Message {
  /**
   * `summary` 是压缩产物（见 agent 的 `SummaryMade`）：它覆盖它之前的全部记录，
   * 界面上渲染成一张「已压缩」的卡片。**原文一条都没删** —— 压缩只是「发给模型
   * 多少」的取舍，不是数据销毁。
   */
  role: 'user' | 'assistant' | 'summary'
  content: string
  /** 摘要覆盖到第几条记录（1-based）。只有 `role === 'summary'` 的记录带它。 */
  covers?: number
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
  /**
   * 这一轮用的模型名。
   *
   * 用量统计要按模型分组，而 `stats` 里只有 token 数、认不出是哪个模型花的 ——
   * 一个会话中途可以换模型，事后也没法反推，所以落盘时就记下来。
   * 老记录没有这个字段，用量页会把它们显示成「—」。
   */
  model?: string
  /**
   * 这一轮列过的任务清单（`todo_write` 的最后一次提交），只用于回看。
   *
   * 运行中的实时进度走 `todo` 事件，不落在这里 —— 这里存的是跑完之后的定稿，
   * 好让回看一条长任务时还看得见它当初打算做哪几步。老记录没有这个字段。
   */
  todos?: TodoItem[]
}

export interface ModelConfig {
  id: string
  name: string
  base_url: string
  /**
   * 协议标识（`openai` / `anthropic` / `ollama` …）。
   *
   * 故意写成 `string` 而不是联合类型：可选协议由 `GET /protocols` 下发，
   * 后端加一个协议时界面不该因为前端这份枚举没跟着改而编译不过。
   */
  protocol: string
  api_key: string
  models: string[]
  /**
   * 模型名 -> 上下文窗口大小（tokens）。
   *
   * 它是**模型级**属性而不是连接级：同一条连接下的多个模型窗口常常不一样
   * （一条中转站可能同时挂着 64k 和 200k 的模型）。某个模型没有条目就是「不知道」，
   * 用量仪表盘会显示「—」，不拿默认值硬凑。
   */
  context_windows: Record<string, number>
}

/** `GET /protocols` 下发的一个协议选项。 */
export interface ProtocolOption {
  value: string
  label: string
  /** 地址留空时后端会用这个默认值；空串表示交给 SDK 用它自己的官方地址。 */
  default_base_url: string
  /** API Key 的填写提示，直接拿来做输入框的 placeholder。 */
  hint: string
}

/** 「连接 + 模型名」摊平后的下拉选项，`key` 就是后端的稳定标识。 */
export interface ModelOption {
  key: string
  config_id: string
  config_name: string
  model: string
  label: string
  /** 该模型的上下文窗口（tokens）；0 表示没填，仪表盘显示「—」。 */
  context_window: number
}

/** `POST /models/test` 拉回来的一个模型。 */
export interface RemoteModel {
  name: string
  /** 解析到的窗口；0 表示没解析到。 */
  context_window: number
  /** 这个值从哪来的：接口返回、本地快照、或没拿到。 */
  source: 'endpoint' | 'catalog' | ''
}

/**
 * 提示词组：从提示词库里挑若干条（可以不挑）拼成的一套提示词。
 *
 * 原先这个东西就叫「模式」，现在模式指四类组的组合，它只是其中提示词那一类。
 * 偏好模型不在这里 —— 搬到模式上了（见 `Mode`）。
 */
export interface PromptGroup {
  id: string
  name: string
  /** 功能简介：和工具组、技能组一致，给模式编辑界面用。 */
  description: string
  /**
   * 提示词 id 列表。允许为空 —— 那就是不带任何系统提示词的纯问答。
   *
   * 用 id 而不是名字：名字可以随便改（改完引用照旧有效），同一个分类也能选多条。
   */
  prompts: string[]
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

/**
 * 提示词库里的一条（元信息 —— 正文要单独按 id 取）。
 *
 * 注意 `name` 只是展示名：它不存在路径里，也不作引用，所以随便改都安全。
 */
export interface PromptItem {
  id: string
  name: string
  category: string
}

/** 提示词库：分类清单 + 全部提示词的元信息。 */
export interface PromptLib {
  categories: string[]
  items: PromptItem[]
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
  /**
   * `tools` 里**每次调用都要用户点头**的那些（是它的子集）。
   *
   * 用来表达「给，但动手前问我」这一档：没有它，`run_command` 这类工具就只能
   * 在「加进组 = 把一台机器交出去」和「不加 = 一点用没有」之间二选一。
   */
  confirm: string[]
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

/**
 * 一个可选的搜索后端。
 *
 * 清单由后端随 `GET /search` 一起给出来，前端不硬编码 —— 多支持一家搜索服务
 * 时只改后端，界面上自动出现。
 */
export interface SearchBackendOption {
  value: string
  label: string
  /** 默认接口地址；界面上当输入框的 placeholder 用。 */
  endpoint: string
  /** Key 去哪领。直接显示给用户，省得再去翻文档。 */
  hint: string
}

export interface SearchConfig {
  backend: string
  /**
   * 后端 -> API Key。
   *
   * 按后端分开存，不是一个字段：Tavily 和博查之间切来切去是常事，
   * 只留一个字段的话，切一次就把另一边填过的 Key 抹掉了。
   */
  keys: Record<string, string>
  /** 覆盖默认端点；空串表示用后端内置的地址（走自建或中转时才改）。 */
  base_url: string
  /** 模型没指定条数时的默认返回条数。 */
  max_results: number
}

/** `GET /search` 的响应。 */
export interface SearchInfo {
  config: SearchConfig
  backends: SearchBackendOption[]
  limits: { min: number; max: number; default: number }
}

/** 一条搜索结果。`snippet` 是摘要，不是正文。 */
export interface SearchHit {
  title: string
  url: string
  snippet: string
}

/** `POST /search/test` 的响应。`ok` 为 false 时 `message` 是失败原因。 */
export interface SearchTestResult {
  ok: boolean
  message: string
  hits: SearchHit[]
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

/** 一个模型的每日用量；`values` 与 `UsageReport.dates` 一一对应。 */
export interface UsageSeries {
  model: string
  values: number[]
}

/** 一个任务的用量汇总（用量页表格的一行）。 */
export interface UsageTask {
  conversation_id: string
  title: string
  /** 这个任务用过的模型名（去重、按首次出现排序）；老记录为空数组。 */
  models: string[]
  /** Unix 时间戳（秒）。展示成什么格式由前端决定。 */
  created_at: number
  tokens: number
  archived: boolean
}

/** `GET /usage` 的响应。折线窗口由 `days` 决定，表格覆盖全部会话。 */
export interface UsageReport {
  dates: string[]
  series: UsageSeries[]
  tasks: UsageTask[]
}

/**
 * 运行中抛给用户的一个问题。
 *
 * 目前只有一种来源：**执行前确认** —— 某个工具被标成「需要确认」（工具组的
 * `confirm`），或者 `run_command` 认出这条命令看着危险。模型主动提问走的是同一条
 * 通道，前端不用改就能支持。
 */
export interface Question {
  /** 哪一次运行在等 —— 回答时要带上它，服务端靠它找到那一轮。 */
  run_id: string
  /** 问题的 id。答案按它匹配；不带的话迟到的答案会落到下一个问题上。 */
  id: string
  /**
   * `confirm`（执行前确认）/ `ask`（模型提问）/ `plan`（审批一份实施方案）。
   *
   * 三者决定卡片长什么样：`confirm` 和 `ask` 都是「要不要做」，`plan` 多一份要读的
   * 正文（用 Markdown 渲染）和一句可选的意见。
   */
  kind: 'confirm' | 'ask' | 'plan'
  text: string
  /**
   * 补充材料：确认题放要执行的命令，计划题放**计划正文**。
   * 用户就是靠它做判断的，所以它不能省。
   */
  detail: string
  /** 可选项；为空表示让用户自由输入。 */
  options: string[]
  /** 等待上限（秒），给倒计时用。 */
  timeout: number
}

/**
 * 子代理干活时播报的一行动静。
 *
 * 子代理有自己的一份上下文，它的中间过程**不进这条消息** —— 所以单独播出来，
 * 否则一个几十秒的 `spawn_agent` 期间界面上什么都没有，看着像卡死了。
 */
export type SubagentEvent =
  | { type: 'tool'; name: string; arguments: string }
  | { type: 'notice'; text: string }

/** 一轮对话里 SSE 推回来的事件。 */
export type ChatEvent =
  /** 这一轮开始，带着它的 id。取消要用它，所以必须最先到。 */
  | { type: 'start'; runId: string }
  | { type: 'text'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'tool'; step: ToolStep }
  | { type: 'notice'; text: string }
  | { type: 'question'; question: Question }
  | { type: 'subagent'; event: SubagentEvent }
  /**
   * 运行中的用量播报：**每请求一次模型就播报一次**，界面据此实时刷新上下文仪表盘。
   *
   * 一轮里模型会被请求多次（每执行完一轮工具就要再问一次），上下文是**一路长上去**的；
   * 这些中间读数既不落盘也不进消息，只是让仪表盘别停在上一次的旧值上。
   */
  | { type: 'usage'; contextTokens: number }
  /**
   * 任务清单更新：模型调用了 `todo_write`。
   *
   * 每次推的都是**完整清单**，前端直接覆盖即可；也**不落盘** —— 落盘的那份随
   * `done` 一起回来（见 `Message.todos`），两者内容相同，但这条要负责「跑的过程中
   * 就能看见进度」。
   */
  | { type: 'todo'; items: TodoItem[] }
  /**
   * 早期历史被压成了摘要。
   *
   * 它**会落盘**（和助手消息一样是一条记录）：刷新之后这张卡片还在 ——
   * 否则用户下次打开会话，发现模型不记得前面的事，只会以为数据被弄丢了。
   */
  | { type: 'summary'; content: string; covers: number; saved: number }
  | { type: 'done'; message: Message }
