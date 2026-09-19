/**
 * 会话级共享状态。
 *
 * 不引 Pinia：需要共享的东西很少（会话列表、当前会话、模型/模式选择），
 * 一个 reactive 对象就够了。等状态多到需要模块化和调试工具时再上不迟。
 *
 * 和 Streamlit 版最大的差别：这些状态活在浏览器里。改一个选择只重渲染用到它的
 * 组件，不会触发整个页面重跑 —— 那正是当初卡顿的根源。
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
  WorkDirInfo,
} from '../api/types'
import { errorText } from '../utils/error'

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

  /** 正在跑一轮对话；期间禁用输入。 */
  busy: false,

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
   * 子代理的实时动静（它调了哪些工具）。
   *
   * 子代理的中间过程**不进这条消息**（那正是它存在的意义），所以单独播一份出来；
   * 它跑完（`spawn_agent` 那一步到达）就清空 —— 成品在工具步骤里，看板没必要留着。
   */
  subagentEvents: [] as SubagentEvent[],
})

/** 后端偏好文件里的键。 */
const PREF_MODEL = 'model'
/**
 * 不带会话前缀的「模式」键。
 *
 * 模式现在按会话记（见 `modeKey`），这个键**不再参与读取** —— 只要还拿它当
 * 回落值，「切到另一个任务却还是上一个任务的模式」就会原样复现。
 * 保留写入是为了 Streamlit 版：那一版读的仍是这个全局键。
 */
const PREF_MODE = 'mode'

/** 某个会话记住的模式 id 的键，与后端 `preferences.mode_key` 是同一口径。 */
function modeKey(conversationId: string): string {
  return `mode::${conversationId}`
}

/**
 * 后端偏好文件的全量快照。
 *
 * 必须留着：切换会话时要就地查出「这个任务记住的是哪个模式」，只在启动时
 * 解出一次的话，切任务时就无从查起了。
 */
let prefs: Record<string, string> = {}

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
  const data = await api.get<{ messages: Message[] }>(`/conversations/${conversationId}`)
  session.currentId = conversationId
  session.messages = data.messages

  // 模式跟着会话走：换任务就把这个任务自己的模式取回来，
  // 而不是把上一个任务的选择带过去（见 restoreMode）
  restoreMode()
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
  // 这里不解析模式：它是按会话记的，而此刻还不知道会落到哪个会话。
  // `loadMessages` 会负责把它取回来
}

/** 启动时把该拿的一次拿齐。 */
export async function bootstrap(): Promise<void> {
  await loadPreferences()
  await Promise.all([loadConversations(), loadOptions()])

  // 接续最近的会话，一个都没有就新建 —— 和 Streamlit 版的 init_state 一个行为
  const recent = session.conversations[0]
  if (recent) await loadMessages(recent.id)
  else await startNewConversation()
}

// ---------------------------------------------------------------------------
// 选择
// ---------------------------------------------------------------------------
export function persistModel(): void {
  // 不 await：偏好是「顺手存一下」，失败了也不该打断对话
  void api.put('/preferences', { values: { [PREF_MODEL]: session.modelKey } })
}

/** 把模式记在某个会话名下；不碰模型选择。 */
function rememberMode(conversationId: string, modeId: string): void {
  const key = modeKey(conversationId)

  // 内存里的快照也要跟着改，否则切回来读到的还是旧值。
  // 全局那份是同写给 Streamlit 版的，Vue 版自己不读它（见 PREF_MODE）
  prefs[key] = modeId
  prefs[PREF_MODE] = modeId
  void api.put('/preferences', { values: { [key]: modeId, [PREF_MODE]: modeId } })
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
 *     onProgress: 有新内容时回调 —— 视图用它把消息区滚到底部。
 *         允许返回 Promise（滚动要等 DOM 更新完），但这里不 await：
 *         滚动是纯展示的事，不该拖慢流式接收。
 */
export async function sendMessage(options: {
  prompt: string
  files: File[]
  onProgress?: () => void | Promise<void>
}): Promise<void> {
  const { prompt, files, onProgress } = options
  const choice = currentModel()

  // 用户消息先上屏，给即时反馈（后端同时也会落盘）。
  // files 只用于在气泡里显示「这条消息带了什么」，不参与发给模型的内容
  session.messages.push({
    role: 'user',
    content: prompt,
    files: files.map((item) => item.name),
  })

  // 一条「正在生成」的占位消息，流式内容直接往里填。
  // 这就是 Vue 相比 Streamlit 的关键差别：改一个字段只重渲染这一条，
  // 不会整页重跑 —— 当初的卡顿正是整页重跑造成的
  const reply = reactive<Message>({ role: 'assistant', content: '', steps: [], notices: [] })
  session.messages.push(reply)

  session.busy = true
  void onProgress?.()

  try {
    for await (const event of streamChat({
      conversation_id: session.currentId,
      prompt,
      model_config_id: choice.config_id,
      model: choice.model,
      mode_id: session.modeId,
      files,
    })) {
      if (event.type === 'start') {
        session.activeRunId = event.runId
      } else if (event.type === 'text') {
        reply.content += event.text
        void onProgress?.()
      } else if (event.type === 'reasoning') {
        reply.reasoning = (reply.reasoning ?? '') + event.text
      } else if (event.type === 'tool') {
        reply.steps?.push(event.step)
        // 子代理那一步到了 = 它跑完了，实时看板收掉（成品已经在这个步骤里）
        if (event.step.name === 'spawn_agent') session.subagentEvents = []
        void onProgress?.()
      } else if (event.type === 'subagent') {
        session.subagentEvents.push(event.event)
        void onProgress?.()
      } else if (event.type === 'notice') {
        reply.notices?.push(event.text)
      } else if (event.type === 'question') {
        // 这一轮会在服务端**卡在这里等答案**，流不会继续往下走。
        // 卡片在消息列表下方，所以要滚一下，否则用户只看到界面停了
        session.pendingQuestion = event.question
        void onProgress?.()
      } else if (event.type === 'done') {
        // 以后端落盘的那份为准：字段更全，也和之后从历史里读出来的一致
        Object.assign(reply, event.message)
      }
    }
  } catch (exc) {
    reply.notices?.push(`请求失败：${errorText(exc)}`)
  } finally {
    session.busy = false
    // 流都结束了还留着一张卡片，用户会以为还能点 —— 点了也没人接
    session.pendingQuestion = null
    // 运行已经收尾，再留着这个 id 只会让「停止」按钮指向一个不存在的运行
    session.activeRunId = ''
    // 被取消 / 出错时子代理看板可能还在，一并收掉
    session.subagentEvents = []
    void onProgress?.()
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
  const runId = session.activeRunId
  if (!runId) return

  try {
    const accepted = await cancelRun(runId)
    if (!accepted) {
      // 和回答一样：没被接受通常是正常竞态（那一轮刚好自己结束了），不弹错误。
      // 真正的失败信号在流那边 —— 收不到 done 才是要管的事
      console.warn('取消请求没有被接受，这一轮可能已经结束')
    }
  } catch (exc) {
    // 网络出错同理：这一轮多半也收不到 done，但那是另一条流自己会处理的事，
    // 不该让一个 rejected promise 冒到点击处理器外面去
    console.warn('取消请求没有发出去：', errorText(exc))
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

  // 先清掉再发：两个按钮都点一下的话，第二个请求会因为 id 对不上而被拒
  session.pendingQuestion = null

  const accepted = await postAnswer(question.run_id, question.id, value)

  if (!accepted) {
    // 没被接受**通常是正常竞态**（点「允许」的同时那一轮刚好超时结束），
    // 所以不弹错误，但必须说一句 —— 不说的话用户会以为答案生效了，
    // 而模型那边收到的其实是「没能问到用户」
    const last = session.messages[session.messages.length - 1]
    last?.notices?.push('这个回答没能送达（这一轮可能已经结束或被中断），模型未必看得到它。')
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
 * 网络请求显然不行。localStorage 零延迟，刷新页面也不丢 —— 比 Streamlit 版
 * 「每字符重跑脚本 + 落盘」的做法合适得多。
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
