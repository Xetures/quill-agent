/**
 * 会话级共享状态。
 *
 * 不引 Pinia：需要共享的东西很少（会话列表、当前会话、模型/模式选择），
 * 一个 reactive 对象就够了。等状态多到需要模块化和调试工具时再上不迟。
 *
 * 这些状态活在浏览器里：改一个选择只重渲染用到它的组件，不会触发整个页面重跑。
 */

import { reactive } from 'vue'

import { answerQuestion as postAnswer, cancelRun, streamChat } from '../api/chat'
import { api } from '../api/client'
import type {
  ConversationBrief,
  Message,
  Mode,
  ModeList,
  ModelOption,
  PickResult,
  Question,
  SubagentEvent,
  TodoItem,
  WorkDirInfo,
} from '../api/types'
import { adoptStoredLocale, i18n, PREF_LANGUAGE } from '../locales'
import { errorText } from '../utils/error'
import { adoptStoredFontSize, PREF_FONT_SIZE } from './font-size'
import { adoptStoredTheme, PREF_THEME } from './theme'

/* 这里不是组件，取不到 useI18n() —— 用实例上的全局 t */
const t = i18n.global.t

export const session = reactive({
  // 会话
  conversations: [] as ConversationBrief[],
  archived: [] as ConversationBrief[],
  currentId: '',
  messages: [] as Message[],

  // 可选项：模型 / 模式 / 工作目录
  models: [] as ModelOption[],
  modes: [] as Mode[],
  /** 三类组 id -> 名字，供模式页显示（后端随 /modes 一起给）。 */
  groupNames: {} as ModeList['groups'],
  workdir: '',

  /**
   * 后端所在的环境能不能拉起系统目录选择器。
   *
   * 本地跑（终端里有图形会话）能；云端部署不能 —— 服务器上弹的窗用户看不到。
   * 界面据此决定点工作目录按钮时走哪条路。
   *
   * 启动时读一次就够了（环境不会中途改变，没必要每次点击都先问一遍）。
   * 注意它和 `PickResult.available` 不是一回事：那个是**本次请求**的实时结论，
   * 两者不一致时以后者为准，见 WorkDirPicker。
   */
  nativePicker: false,

  // 当前选择。模型用 "连接id::模型名" 这个稳定标识 —— 和模式里的偏好模型
  // 同一口径，两边可以直接互相赋值（理由见后端的 model_choice_key）
  modelKey: '',
  modeId: '',

  /**
   * 要不要让模型思考（推理模型的思维链）。
   *
   * 关掉它是给小模型用的：它们常常一思考就把输出预算花光，正文一个字都给不出来。
   * 关闭时后端会带上 `reasoning_effort="none"`（见 `agent._open_stream`）。
   *
   * 是**全局偏好**而不是按会话记：它跟着「你在用哪一类模型」走，而不是某一个任务的属性。
   */
  thinking: true,

  /** 正在跑一轮对话；期间禁用输入。 */
  busy: false,

  /**
   * 运行中播报的最新上下文读数（来自流里的 `usage` 事件）。
   *
   * 为什么需要它：一轮里模型会被请求多次（每执行完一轮工具再问一次），上下文一直在长，
   * 而这些中间读数要等这一轮**落盘**（`done`）才会出现在消息的 `stats` 上 —— 在那之前
   * 仪表盘只能读到上一轮的旧值，整个过程都不动，跑完才跳一下。
   *
   * 0 表示「还没播报过」，仪表盘退回读消息上的 `stats`。跑完之后**不清零**：它和刚落下
   * 的那个值本来就相等，留着反而能保住「中途出错 / 被取消」那一轮已经长到的大小，
   * 不至于掉回上一轮的读数。换会话时必须清 —— 那是另一份上下文。
   */
  liveUsage: 0,

  /**
   * 运行中播报的**缓存命中** token 数 —— 和 `liveUsage` 来自**同一次请求**。
   *
   * 界面上那个「缓存命中率」就是两者相除。这里存原始数而不是存比率：它们的更新时机
   * 和保留策略跟 `liveUsage` 完全一致，比率等读的时候再算更简单。
   */
  liveCached: 0,

  /**
   * 运行中播报的**轮次进度**（来自流里的 `round` 事件）；没在跑就是 null。
   *
   * 为什么单独要它：上面两个读数都依赖服务端返回用量，而有些服务不返回；
   * 轮次是**我们自己数的**，一定有。所以「跑了多少、还剩多少余地」这件事靠它 ——
   * 一次运行可能十几分钟、几十轮，没有它只能盯着不动的界面猜。
   *
   * 跑完清掉（下一次跑是新的进度），换会话跟着 `attachRun` 走。
   */
  liveRound: null as { index: number; total: number } | null,

  /**
   * 这一轮开始跑的时刻（`Date.now()`），没在跑就是 0。
   *
   * 供界面算「已用时间」。刻意由状态持有而不是让组件自己记：切走会话再回来时，
   * 组件是重挂的，自己记就会**从头计时**，而这一轮其实已经跑了一半。
   */
  liveStartedAt: 0,

  /**
   * 运行中抛回来、还没回答的问题（目前只有执行前确认）。
   *
   * 挂在会话上而不是那条流式消息上：它要在消息列表**下方**渲染成一张卡片，
   * 答完就消失。混进消息里的话，它会跟着消息一起写进会话文件 ——
   * 回看历史时会看到一串早已失效的按钮。
   */
  pendingQuestion: null as Question | null,

  /**
   * 正在跑的运行 id（来自流开头的 `start` 事件）。空串表示没有在跑。
   *
   * 必须存下来：取消接口要它，而它是**运行开始时才生成的** —— 前端除了从流里接住
   * 没有别的办法知道。
   */
  activeRunId: '',

  /**
   * 当前会话跑到哪一步了；没在跑就是 null（见 `RunPhase`）。
   *
   * 它回答的是「是不是卡了」：事件流在几处会静默（等模型吐第一个字、工具执行中），
   * 没有它的话，用户只能盯着不动的界面猜。
   */
  phase: null as RunPhase | null,

  /**
   * 哪些会话正在跑（会话 id 列表）。侧边栏拿它在会话名旁边转个圈 ——
   * 否则切走之后，用户根本不知道那个任务还在不在跑。
   *
   * 数据源是模块级的 `runs` Map，但它不是响应式的（里面装着 AbortController 这类
   * 东西，不该被 Vue 代理一层）。所以每次增删都同步一份 id 过来 —— 界面上要渲染的
   * 本来也就只是这几个 id。
   */
  runningIds: [] as string[],

  /**
   * 子代理的实时动静（它调了哪些工具）。
   *
   * 子代理的中间过程**不进这条消息**（那正是它存在的意义），所以单独播一份出来；
   * 它跑完（`spawn_agents` 那一步到达）就清空 —— 成品在工具步骤里，看板没必要留着。
   */
  subagentEvents: [] as SubagentEvent[],

  /**
   * 运行中的任务清单（来自流里的 `todo` 事件）。
   *
   * 为什么不直接写进那条消息：清单在这一轮里会被**反复整体替换**，写进消息就得和
   * 流式正文抢同一个字段；而且中途出错时，那份残缺的清单会和落盘的定稿混在一起，
   * 分不清哪个算数。挂在这里，界面贴着一张实时的进度卡，和消息各归各。
   *
   * 什么时候清 —— 三处，各有各的理由：
   *
   *     跑完（收到 `done`）：定稿已随消息落盘、卡片移进消息里，不留两份；
   *     换会话：那是另一个任务的计划；
   *     开始下一轮：收掉上一轮留下的（被取消的那轮会留着，见下条）。
   *
   * **出错 / 被取消时不清**：那正是最需要看见「还剩哪几步」的时候，收掉反而把信息
   * 抹了。道理同 `liveUsage`。
   */
  liveTodos: [] as TodoItem[],
})

/** 后端偏好文件里的键。 */
const PREF_MODEL = 'model'

/** 全局偏好：要不要让模型思考（"1"/"0"）。缺省 / 坏值一律当**开**，与历史行为一致。 */
const PREF_THINKING = 'thinking'

/** 某个会话记住的模式 id 的键，与后端 `preferences.mode_key` 是同一口径。 */
function modeKey(conversationId: string): string {
  return `mode::${conversationId}`
}

/**
 * 某个会话记住的模型选择（值形如 `连接id::模型名`，和模式同一套口径）。
 *
 * 模型原先只有一份**全局**偏好，于是切任务时带过去的是上一个任务的模型 —— 可模型是
 * 「这个任务用什么脑子」，和模式一样属于任务级的选择，不该只跟一个走。
 *
 * 全局那份仍然保留，但改作**新任务的起点**：新会话还没有自己的记录，就拿它兜底，
 * 这样刚选的模型能顺延到下一个任务，而不是掉回列表第一个。
 */
function modelKeyFor(conversationId: string): string {
  return `model::${conversationId}`
}

/**
 * 后端偏好文件的全量快照。
 *
 * 必须留着：切换会话时要就地查出「这个任务记住的是哪个模式」，只在启动时
 * 解出一次的话，切任务时就无从查起了。
 */
let prefs: Record<string, string> = {}

/**
 * 这一轮现在卡在哪一步。
 *
 * 存在的意义就是回答「它是不是卡了」。事件流有几处是静默的：从请求发出到模型吐第一个字
 * （首字延迟十几秒很常见）、工具执行中（联网搜索、扫大目录）。没有这个，用户只能盯着
 * 不动的界面猜自己该不该刷新。
 */
export type RunPhase =
  /** 刚发出请求，还没连上。 */
  | { kind: 'connecting' }
  /** 连上了，在等模型吐第一块。 */
  | { kind: 'waiting' }
  /** 推理模型的思维链在往外走。 */
  | { kind: 'thinking' }
  /** 正文在往外走。 */
  | { kind: 'generating' }
  /** 某个工具正在执行（带名字）。 */
  | { kind: 'tool'; name: string }
  /** 停在等用户回答（确认 / 提问 / 计划审批）。 */
  | { kind: 'asking' }

/** 一次正在跑的运行的完整状态。 */
interface ActiveRun {
  conversationId: string
  /** 当前跑到哪一步（见 `RunPhase`）。 */
  phase: RunPhase
  /** 运行 id，要等流开头的 `start` 事件才拿得到；拿到之前是空串。 */
  runId: string
  /** 正在生成的那条助手消息。切回来时要重新挂回 `session.messages`。 */
  reply: Message
  controller: AbortController
  usage: number
  /** 同一次请求里缓存命中的输入 token 数（缓存命中率的分子）。 */
  cached: number
  /**
   * 这一轮开始跑的时刻（`Date.now()`）。
   *
   * 只存起点、不存「已用多少秒」：用时得**每秒重算**才走字，那是展示层的事。
   * 存成状态里的一个滚动数字反而要配定时器，还得处理切会话、跑完这些收尾。
   */
  startedAt: number
  /** 跑到第几圈（来自 `round` 事件）；还没收到就是 null。 */
  round: { index: number; total: number } | null
  todos: TodoItem[]
  subagentEvents: SubagentEvent[]
  question: Question | null
}

/**
 * 正在跑的运行，按会话存。
 *
 * **运行跟着会话走，不跟着界面走。** 切走会话只是「暂时看不到它」，而不是「把它停掉」——
 * 这才是符合直觉的行为：去看一眼别的任务，回来时它该还在跑，而不是被掐断、助手回复
 * 消失、上下文读数归零。
 *
 * 之前这里是单个 `activeController`，切会话时直接 `abort()` —— 那个设计把界面和运行
 * 绑成了一件事，于是「切出去再切回来」等于把这一轮丢掉。
 *
 * 用 Map 而不是单值还有一层好处：两个会话可以各跑各的（后端本来就是一请求一 run），
 * 谁也不会覆盖谁。
 */
const runs = new Map<string, ActiveRun>()

/**
 * 把「哪些会话在跑」同步给响应式状态（侧边栏拿它转圈）。
 *
 * 每次 `runs` 增删之后都要调：忘了的话，跑起来的会话旁边不会出现转圈图标 ——
 * 而这个图标正是「我切走了，那个任务还在跑」的唯一提示。
 */
function syncRunningIds(): void {
  session.runningIds = [...runs.keys()]
}

/**
 * 「加载某个会话」的请求序号，用来丢弃过期响应。
 *
 * 没有它就会有这个 bug：连着点会话 A、B，若 A 的响应比 B 晚回来，
 * `currentId` / `messages` 会被 A 覆盖 —— 界面停在一个已经切走的会话上。
 */
let loadToken = 0

/**
 * 界面切到某个会话时，把实时状态接成该会话那一份。
 *
 * 没有运行就把几个实时字段清空（它们描述的必须是**当前会话**的状态）；有运行就恢复它的
 * 读数、清单、待答问题，并把还没落盘的那条助手消息重新挂回消息列表 —— 否则切回来只看得到
 * 自己的提问，助手的回复要等这一轮结束落盘之后才会出现。
 */
function attachRun(conversationId: string): void {
  const run = runs.get(conversationId)

  session.busy = Boolean(run)
  session.activeRunId = run?.runId ?? ''
  session.phase = run?.phase ?? null
  session.liveUsage = run?.usage ?? 0
  session.liveCached = run?.cached ?? 0
  session.liveRound = run?.round ?? null
  session.liveStartedAt = run?.startedAt ?? 0
  session.liveTodos = run?.todos ?? []
  session.subagentEvents = run?.subagentEvents ?? []
  session.pendingQuestion = run?.question ?? null

  // 正在生成的那条不在服务端返回的历史里（还没落盘），得手动接上：先把可能存在的
  // 同一个对象滤掉，再追加到末尾。这样写是幂等的 —— 反复切回来也只会有一条，
  // 而且它一定在最后（顺序也对）
  if (run) {
    session.messages = [...session.messages.filter((item) => item !== run.reply), run.reply]
  }
}

/** 事件到达时，把界面上的实时状态同步成这个运行的最新值（仅当正看着这个会话）。 */
function syncRunState(run: ActiveRun): void {
  if (run.conversationId !== session.currentId) return

  session.phase = run.phase
  session.liveUsage = run.usage
  session.liveCached = run.cached
  session.liveRound = run.round
  session.liveStartedAt = run.startedAt
  session.liveTodos = run.todos
  session.subagentEvents = run.subagentEvents
  session.pendingQuestion = run.question
}

/**
 * 把一段流式增量并进 `parts`：接在**同类型**的最后一段后面，类型变了就另起一段。
 *
 * 这份顺序是界面穿插显示的唯一依据 —— `content` 和 `steps` 是分开存的，相对顺序在后
 * 端落盘时就丢了（见 `MessagePart`）。这里和后端 `stream_round` 里的封段规则一致：
 * 连续的正文分片合成一段，一次工具调用自成一格。
 */
function appendPart(reply: Message, type: 'text' | 'reasoning', text: string): void {
  const parts = reply.parts ?? (reply.parts = [])
  const last = parts[parts.length - 1]

  if (last && last.type === type) {
    last.content += text
    return
  }
  parts.push({ type, content: text })
}

/**
 * 换一个阶段。
 *
 * 同一种阶段不重复写：`text` 事件每个分片来一次，而 `generating` → `generating`
 * 这种无谓的赋值会白白触发一轮重渲染（phase 是个新对象，Vue 认不出它们相等）。
 * `tool` 例外 —— 连着两个工具时名字要跟着换。
 */
function setPhase(run: ActiveRun, phase: RunPhase): void {
  if (run.phase.kind === phase.kind && phase.kind !== 'tool') return

  run.phase = phase
  syncRunState(run)
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------
export async function loadConversations(): Promise<void> {
  const data = await api.get<{ active: ConversationBrief[]; archived: ConversationBrief[] }>(
    '/conversations',
  )
  session.conversations = data.active
  session.archived = data.archived
}

export async function loadMessages(conversationId: string): Promise<void> {
  const token = ++loadToken
  const data = await api.get<{ messages: Message[] }>(`/conversations/${conversationId}`)

  // 先发的那次可能后回来（见 loadToken）：这份数据描述的已经不是当前会话了
  if (token !== loadToken) return

  session.currentId = conversationId
  session.messages = data.messages

  // 模式和模型都跟着会话走：换任务就把这个任务自己的选择取回来，
  // 而不是把上一个任务的选择带过去（见 restoreMode / restoreModel）
  restoreMode()
  restoreModel()

  // 这个会话可能**还在跑**（切走时并没有停掉它）：把实时读数、清单、待回答的问题和
  // 正在生成的那条消息都接回来；没在跑则一并清空 —— 那几个字段描述的必须是当前会话
  // 的状态，不能留着上一个会话的
  attachRun(conversationId)
}

export async function loadOptions(): Promise<void> {
  const [models, modes, workdir] = await Promise.all([
    api.get<{ options: ModelOption[] }>('/models'),
    api.get<ModeList>('/modes'),
    api.get<WorkDirInfo>('/workdir'),
  ])

  session.models = models.options
  session.modes = modes.modes
  session.groupNames = modes.groups
  session.workdir = workdir.path
  session.nativePicker = workdir.native_picker

  // 存下来的选择可能指向已经被删掉的模型 / 模式，校验一遍再采用
  if (!session.models.some((item) => item.key === session.modelKey)) {
    session.modelKey = session.models[0]?.key ?? ''
  }
  // 模式是必选的（不像模型还有「没配模型」这种合理状态）：一个都没配时留空，
  // 由任务页提示去模式页创建，而不是悄悄退化成「不用模式」
  if (!session.modes.some((item) => item.id === session.modeId)) {
    session.modeId = session.modes[0]?.id ?? ''
  }
}

export async function loadPreferences(): Promise<void> {
  prefs = await api.get<Record<string, string>>('/preferences')
  session.modelKey = prefs[PREF_MODEL] ?? ''
  // 只有明确写了 "0" 才算关 —— 缺省、空串、手滑写坏的值都当开
  session.thinking = prefs[PREF_THINKING] !== '0'
  // 语言：只在本地没存过时才采纳后端那份（换了浏览器、清了站点数据的情况）。
  // 本地有值就听本地的 —— 那是更新的一次选择
  adoptStoredLocale(prefs[PREF_LANGUAGE])
  // 主题与字号：同一套规则（见那两个 store 里的说明）。localStorage 是首屏那一帧要
  // 用的，偏好是换环境之后还在的那一份；两边都在时听本地的。
  adoptStoredTheme(prefs[PREF_THEME])
  adoptStoredFontSize(prefs[PREF_FONT_SIZE])

  // 这里不解析模式：它是按会话记的，而此刻还不知道会落到哪个会话。
  // `loadMessages` 会负责把它取回来
}

/** 启动时把该拿的一次拿齐。 */
export async function bootstrap(): Promise<void> {
  await loadPreferences()
  await Promise.all([loadConversations(), loadOptions()])

  // 接续最近的会话，一个都没有就新建
  const recent = session.conversations[0]
  if (recent) await loadMessages(recent.id)
  else await startNewConversation()
}

// ---------------------------------------------------------------------------
// 选择
// ---------------------------------------------------------------------------
export function persistModel(): void {
  // 两处一起写：按会话记一份（切回这个任务时要用，见 `restoreModel`），
  // 全局那份刷新一下（留给**新任务**兜底）。
  // 会话 id 还没落地时只写全局 —— 别写出 `model::` 这种没有下文的键
  const values: Record<string, string> = { [PREF_MODEL]: session.modelKey }
  if (session.currentId) values[modelKeyFor(session.currentId)] = session.modelKey

  // 不 await：偏好是「顺手存一下」，失败了也不该打断对话
  void api.put('/preferences', { values })
}

/** 记下思考开关。和 `persistModel` 一样是全局偏好。 */
export function persistThinking(): void {
  void api.put('/preferences', { values: { [PREF_THINKING]: session.thinking ? '1' : '0' } })
}

/** 把模式记在某个会话名下；不碰模型选择。 */
function rememberMode(conversationId: string, modeId: string): void {
  const key = modeKey(conversationId)

  // 内存里的快照也要跟着改，否则切回来读到的还是旧值
  prefs[key] = modeId
  void api.put('/preferences', { values: { [key]: modeId } })
}

/** 用户主动切模式：记下来，并把模式绑定的偏好模型一并套上。 */
export function persistMode(): void {
  rememberMode(session.currentId, session.modeId)
  applyPreferredModel()
}

/**
 * 取回当前会话记住的模式。
 *
 * 模式是「任务级」的：任务 A 用模式 A 聊，切到任务 B 就该是 B 自己的模式，
 * 而不是把 A 的选择带过去。所以这里**只认这个会话自己的记录**，没记过就落到
 * 默认模式（第一个）—— 绝不拿全局的「上次用过什么」来兜底，那正是这个 bug 本身。
 *
 * 存下来的模式可能已被删掉（模式页删得掉），所以要对着现有模式校验一遍。
 *
 * 这里刻意**不套用模式的偏好模型**：那会让「切个任务」顺带把用户手选的模型换掉，
 * 而用户要的只是模式跟着任务走。偏好模型只在主动切模式时才生效。
 */
function restoreMode(): void {
  const remembered = prefs[modeKey(session.currentId)] ?? ''
  const valid = session.modes.some((item) => item.id === remembered)

  session.modeId = valid ? remembered : (session.modes[0]?.id ?? '')
}

/**
 * 取回当前会话记住的模型。和 `restoreMode` 同一时机、同一道理。
 *
 * 取用顺序：**这个任务自己的记录 → 全局那份（新任务的起点）→ 列表第一个**。
 * 存下来的可能已被删掉（模型页删得掉），所以对着现有模型校验一遍。
 */
function restoreModel(): void {
  const remembered = prefs[modelKeyFor(session.currentId)] ?? prefs[PREF_MODEL] ?? ''

  session.modelKey = session.models.some((item) => item.key === remembered)
    ? remembered
    : (session.models[0]?.key ?? '')
}

/**
 * 模式可以绑定一个偏好模型：切过去时跟着换，没配就保持当前选择不动。
 * 偏好模型若已被删掉就跳过 —— 别让选择器指向一个不存在的值。
 */
function applyPreferredModel(): void {
  const mode = session.modes.find((item) => item.id === session.modeId)
  if (mode?.preferred_model && session.models.some((item) => item.key === mode.preferred_model)) {
    session.modelKey = mode.preferred_model
    persistModel()
  }
}

/** 把 "连接id::模型名" 拆成后端要的两部分。 */
export function currentModel(): { config_id: string; model: string } {
  const [config_id = '', model = ''] = session.modelKey.split('::')
  return { config_id, model }
}

// ---------------------------------------------------------------------------
// 工作目录
// ---------------------------------------------------------------------------

/**
 * 切换工作目录。
 *
 * 它同时是文件工具的安全边界，所以后端会校验两件事：目录确实存在；会话还是空的。
 * 这里不吞这个错 —— 调用方需要把后端的文案原样显示出来。
 */
export async function changeWorkdir(path: string): Promise<void> {
  const data = await api.put<{ path: string }>('/workdir', {
    path,
    // 后端不知道当前在聊哪个会话（无状态），得由界面带上才能做「空会话才让换」的校验
    conversation_id: session.currentId,
  })
  session.workdir = data.path
}

/**
 * 拉起系统目录选择器。
 *
 * **这是个慢请求**：后端会一直阻塞到用户关掉对话框（见 `pick_dir_with_system_dialog`）。
 * 调用方要显示等待状态，否则用户会以为按钮点空了。
 */
export async function pickWorkdir(): Promise<PickResult> {
  return api.post<PickResult>('/workdir/pick')
}

/** 清除界面选择，回到配置里的默认工作目录。 */
export async function resetWorkdir(): Promise<void> {
  const data = await api.del<{ path: string }>('/workdir')
  session.workdir = data.path
}

// ---------------------------------------------------------------------------
// 发消息
// ---------------------------------------------------------------------------

/**
 * 发一条消息，把流式事件填进消息列表。
 *
 * 从视图里搬出来，是因为这里装着「一轮对话怎么组装成的」全部知识：用户消息、
 * 占位消息、四类事件各落到哪个字段、完成时以哪份数据为准。留一半在组件里的话，
 * 读的人得同时翻 `api/chat.ts` 和 `ChatView.vue` 才拼得出全貌。
 *
 * Args:
 *     prompt: 本轮输入（调用方负责 trim）。
 *     files: 本轮附件；会随请求上传并落到工作目录。
 *
 * 这里**不**给「有新内容」的回调：跟随滚动是视图的事，而它靠盯住消息区的高度
 * 来判断，不靠这些事件 —— 见 ChatView.vue 里为什么那么做。
 */
export async function sendMessage(options: {
  prompt: string
  files: File[]
}): Promise<void> {
  const { prompt, files } = options
  const choice = currentModel()
  // 记下这一轮属于哪个会话：跑的过程中用户可能切走，切走了就不该再往界面上灌
  // 这条流的读数（见 syncRunState）
  const conversationId = session.currentId

  // 同一会话不并发：一轮还没跑完就再发一条，`runs` 里这条记录会被后来的**覆盖**，
  // 而先结束的那一轮又会把后来者的记录删掉 —— 两轮互相踩，界面收不了尾
  // （卡在「等待模型响应」、输入框锁死，只能刷新）。
  //
  // 界面上输入框在 busy 时本就禁用，这里是兜底：绕过界面直接调用（脚本、自动化）
  // 那条路也得被拦住。而且要在消息上屏**之前**拦 —— 否则会留下一条永远没有回复的提问。
  if (runs.has(conversationId)) return

  // 用户消息先上屏，给即时反馈（后端同时也会落盘）。
  // files 只用于在气泡里显示「这条消息带了什么」，不参与发给模型的内容
  session.messages.push({
    role: 'user',
    content: prompt,
    files: files.map((item) => item.name),
  })

  // 一条「正在生成」的占位消息，流式内容直接往里填。
  // 改一个字段只重渲染这一条，不会整页重跑
  // `parts` 从一开始就是空数组：正文/思考/工具按发生顺序往里加（见 appendPart）
  const reply = reactive<Message>({
    role: 'assistant',
    content: '',
    steps: [],
    notices: [],
    parts: [],
  })
  session.messages.push(reply)

  // 这一轮的全部状态。挂在 `runs` 里而不是散在 session 上：用户切走时它要跟着这一轮
  // 留下来，切回来时再挂回界面（见 attachRun）
  const run: ActiveRun = {
    conversationId,
    // 起点是「还没连上」：请求刚发出去，`start` 事件还没回来
    phase: { kind: 'connecting' },
    runId: '',
    reply,
    controller: new AbortController(),
    usage: 0,
    cached: 0,
    startedAt: Date.now(),
    round: null,
    todos: [],
    subagentEvents: [],
    question: null,
  }
  runs.set(conversationId, run)
  // 侧边栏立刻就能在这个会话旁边转圈 —— 不用等第一个事件回来
  syncRunningIds()

  // 上一轮的清单可能还挂着（被取消的那轮会刻意留着），这一轮重新开始，收掉它
  session.liveTodos = []

  session.busy = true
  syncRunState(run)

  try {
    for await (const event of streamChat(
      {
        conversation_id: conversationId,
        prompt,
        model_config_id: choice.config_id,
        model: choice.model,
        mode_id: session.modeId,
        thinking: session.thinking,
        files,
      },
      run.controller.signal,
    )) {
      if (event.type === 'start') {
        run.runId = event.runId
        if (run.conversationId === session.currentId) session.activeRunId = event.runId
        setPhase(run, { kind: 'waiting' })
      } else if (event.type === 'text') {
        // 正文直接写进 reply：它是个 reactive 对象，挂回消息列表时自然会渲染。
        // 注意这里**不看当前是哪个会话** —— 用户切走了也要照常累积，
        // 切回来时才能看到完整的这一轮（见 attachRun）
        reply.content += event.text
        appendPart(reply, 'text', event.text)
        setPhase(run, { kind: 'generating' })
      } else if (event.type === 'reasoning') {
        reply.reasoning = (reply.reasoning ?? '') + event.text
        appendPart(reply, 'reasoning', event.text)
        setPhase(run, { kind: 'thinking' })
      } else if (event.type === 'round') {
        // 第几圈了。它是**我们自己数的** —— 不像 `usage` 要等服务端返回用量
        // （有些服务根本不给），所以「跑了多少、还剩多少余地」这件事只能靠它
        run.round = { index: event.index, total: event.total }
        syncRunState(run)
      } else if (event.type === 'usage') {
        // 运行中的实时读数：先记进这一轮，再（在当前会话时）同步给仪表盘
        run.usage = event.contextTokens
        run.cached = event.cachedTokens
        syncRunState(run)
      } else if (event.type === 'tool_start') {
        setPhase(run, { kind: 'tool', name: event.name })
      } else if (event.type === 'tool') {
        reply.steps?.push(event.step)
        // 顺序：这一次调用自成一格，插在当前序列的末尾（见 appendPart 的说明）
        const parts = reply.parts ?? (reply.parts = [])
        parts.push({ type: 'tool', step: event.step })
        // 工具跑完了：下一步是「把结果发回模型、等它接着想」—— 阶段回到等待
        setPhase(run, { kind: 'waiting' })
        // 子代理那一步到了 = 它跑完了，实时看板收掉（成品已经在这个步骤里）
        if (event.step.name === 'spawn_agents') {
          run.subagentEvents = []
          syncRunState(run)
        }
      } else if (event.type === 'todo') {
        run.todos = event.items
        syncRunState(run)
      } else if (event.type === 'subagent') {
        // **换成新数组，不要原地 push。** `syncRunState` 是靠**赋值**把状态同步到界面的，
        // 而给它喂同一个引用时 Vue 的 `hasChanged` 判定「没变」、不触发更新 —— 原地 push
        // 之后看板永远不出现（这些事件明明都收到了）。
        // 其余实时字段没这个问题：它们每次都是整个换掉（新值 / 新数组），引用一定会变
        run.subagentEvents = [...run.subagentEvents, event.event]
        syncRunState(run)
      } else if (event.type === 'summary') {
        // 压缩发生在「组装上下文」阶段，比正文来得更早 —— 所以它插在当前这条正在生成的
        // 助手消息**之前**，那正是它在时间线上的真实位置。
        // 切走了就不动界面：后端已经落盘，切回来 loadMessages 自然会读到
        if (session.currentId === conversationId) {
          const at = session.messages.indexOf(reply)
          session.messages.splice(at < 0 ? session.messages.length : at, 0, {
            role: 'summary',
            content: event.content,
            covers: event.covers,
          })
        }
      } else if (event.type === 'notice') {
        reply.notices?.push(event.text)
      } else if (event.type === 'question') {
        // 这一轮会在服务端**卡在这里等答案**，流不会继续往下走
        run.question = event.question
        setPhase(run, { kind: 'asking' })
      } else if (event.type === 'done') {
        // 以后端落盘的那份为准：字段更全，也和之后从历史里读出来的一致
        Object.assign(reply, event.message)
        // 清单的定稿已经随这条消息落地、由 MessageItem 渲染，实时卡片收掉 —— 两份
        // 长一样的东西同时挂着，用户只会以为任务跑了两遍
        run.todos = []
        syncRunState(run)
      }
    }
  } catch (exc) {
    // 用户自己点了「停止」不算失败：在气泡里留一条「请求失败」
    // 只会让他以为出了故障
    if (!run.controller.signal.aborted) reply.notices?.push(t('session.requestFailed', { detail: errorText(exc) }))
  } finally {
    // 只清理**属于自己那一份**：`runs` 按会话存，万一已有更晚的一轮接管了它
    // （同会话并发时 `set` 会覆盖），无条件 delete 会把对方的记录一并抹掉 ——
    // 侧边栏转圈消失、界面提前解锁，而那一轮其实还在跑。
    // 正常路径下这里取到的就是自己，删完即空。
    const owns = runs.get(conversationId) === run
    if (owns) runs.delete(conversationId)
    // 转圈图标按实际的 runs 重算（也可能这一轮早就不在当前会话上了）
    syncRunningIds()

    // 只有界面正看着这个会话时才收拾实时状态。用户切到别的会话去了的话，
    // 就不该动那一边的界面 —— 那边自有它自己的一份（见 attachRun）
    if (session.currentId === conversationId) {
      const still = runs.get(conversationId)
      if (still) {
        // 同会话还有一轮在跑（异常的并发路径）：把界面接回它，**别解锁**。
        // 提前解锁会让用户以为能发下一条，而那一轮其实还没完
        syncRunState(still)
      } else {
        session.busy = false
        session.phase = null
        // 流都结束了还留着一张卡片，用户会以为还能点 —— 点了也没人接
        session.pendingQuestion = null
        // 运行已经收尾，再留着这个 id 只会让「停止」按钮指向一个不存在的运行
        session.activeRunId = ''
        // 轮次是这一轮的进度，跑完就作废（下一轮从头数）
        session.liveRound = null
        session.liveStartedAt = 0
        // 被取消 / 出错时子代理看板可能还在，一并收掉
        session.subagentEvents = []
        // liveUsage / liveTodos 刻意不清：那一轮已经长到多大，正是出错后想看的（见字段注释）
      }
    }

    // 标题和排序时间可能变了，刷一下侧边栏
    void loadConversations()
  }
}

/**
 * 停止正在跑的这一轮。
 *
 * **不在这里把 `busy` 改成 false。** 服务端的取消是「请求停止」：标记立起来之后，
 * agent 循环在下一个检查点退出，中间可能还要跑一小会儿（正在等的那次模型请求会先结束）。
 * 提前解锁输入框会让用户以为可以发下一条，而那一轮其实还在收尾。
 * 真正的结束信号始终是流里收到 `done` —— 到时候 `sendMessage` 的 finally 会收拾状态。
 */
export async function stopRun(): Promise<void> {
  // 取当前会话自己的运行：用户可能切到了别的任务上，那个任务的「停止」
  // 不该停掉另一个会话里正在跑的这一轮
  const runId = runs.get(session.currentId)?.runId ?? ''
  if (!runId) return

  try {
    const accepted = await cancelRun(runId)
    if (!accepted) {
      // 和回答一样：没被接受通常是正常竞态（那一轮刚好自己结束了），不弹错误。
      // 真正的失败信号在流那边 —— 收不到 done 才是要管的事
      console.warn(t('session.cancelIgnored'))
    }
  } catch (exc) {
    // 网络出错同理：这一轮多半也收不到 done，但那是另一条流自己会处理的事，
    // 不该让一个 rejected promise 冒到点击处理器外面去
    console.warn(t('session.cancelFailed'), errorText(exc))
  }
}

/**
 * 回答运行中抛出的一个问题。
 *
 * 答完**不重发 `/chat`** —— 那一轮还在服务端跑着，只是卡在等这个答案上。
 * 我们只把答案递过去，然后继续从原来那条流里收后续事件。
 */
export async function answerQuestion(value: string): Promise<void> {
  const question = session.pendingQuestion
  if (!question) return

  // 先清掉再发：两个按钮都点一下的话，第二个请求会因为 id 对不上而被拒。
  // 这一轮里那份也要清 —— 否则切走再切回来，attachRun 会把它重新挂上，
  // 而那个问题其实已经答过了
  session.pendingQuestion = null
  const run = runs.get(session.currentId)
  if (run) run.question = null

  const accepted = await postAnswer(question.run_id, question.id, value)

  if (!accepted) {
    // 没被接受**通常是正常竞态**（点「允许」的同时那一轮刚好超时结束），
    // 所以不弹错误，但必须说一句 —— 不说的话用户会以为答案生效了，
    // 而模型那边收到的其实是「没能问到用户」
    const last = session.messages[session.messages.length - 1]
    last?.notices?.push(t('session.notDelivered'))
  }
}

// ---------------------------------------------------------------------------
// 会话操作
// ---------------------------------------------------------------------------
export async function startNewConversation(): Promise<void> {
  // 新任务沿用当前模式：刚在别处选好的模式，多半还想接着用。
  // 但要把它写进新会话自己的键 —— 之后改这个任务的模式，不该回头影响别的任务
  const inherited = session.modeId

  const { id } = await api.post<{ id: string }>('/conversations')
  await loadMessages(id) // 这里会先把模式置成默认值

  if (session.modes.some((item) => item.id === inherited)) {
    session.modeId = inherited
    rememberMode(id, inherited)
  }

  await loadConversations()
}

export async function archiveConversation(conversationId: string): Promise<boolean> {
  const { archived } = await api.post<{ archived: boolean }>(
    `/conversations/${conversationId}/archive`,
  )
  clearDraft(conversationId)
  await loadConversations()

  // 归档的正好是当前会话，就切到剩下的第一个；一个都不剩就新建
  if (conversationId === session.currentId) {
    const next = session.conversations[0]
    if (next) await loadMessages(next.id)
    else await startNewConversation()
  }

  return archived
}

// ---------------------------------------------------------------------------
// 草稿：按会话隔离，存在浏览器本地
// ---------------------------------------------------------------------------
/**
 * 为什么不走后端的偏好文件：草稿是「打字过程中」的状态，每敲一个字就发一次
 * 网络请求显然不行。localStorage 零延迟，刷新页面也不丢。
 */
export function loadDraft(conversationId: string): string {
  return localStorage.getItem(draftKey(conversationId)) ?? ''
}

export function saveDraft(conversationId: string, text: string): void {
  const key = draftKey(conversationId)
  // 空草稿直接删键，别在 localStorage 里堆一串空字符串
  if (text) localStorage.setItem(key, text)
  else localStorage.removeItem(key)
}

export function clearDraft(conversationId: string): void {
  localStorage.removeItem(draftKey(conversationId))
}

function draftKey(conversationId: string): string {
  return `quill:draft:${conversationId}`
}
