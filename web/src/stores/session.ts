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

import { streamChat } from '../api/chat'
import { api } from '../api/client'
import type {
  ConversationBrief,
  Message,
  ModelOption,
  PickResult,
  PromptMode,
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
  modes: [] as PromptMode[],
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
})

/** 后端偏好文件里的键。 */
const PREF_MODEL = 'model'
const PREF_MODE = 'mode'

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
}

export async function loadOptions(): Promise<void> {
  const [models, modes, workdir] = await Promise.all([
    api.get<{ options: ModelOption[] }>('/models'),
    api.get<{ modes: PromptMode[] }>('/modes'),
    api.get<WorkDirInfo>('/workdir'),
  ])

  session.models = models.options
  session.modes = modes.modes
  session.workdir = workdir.path
  session.nativePicker = workdir.native_picker

  // 存下来的选择可能指向已经被删掉的模型 / 模式，校验一遍再采用
  if (!session.models.some((item) => item.key === session.modelKey)) {
    session.modelKey = session.models[0]?.key ?? ''
  }
  if (!session.modes.some((item) => item.id === session.modeId)) {
    session.modeId = session.modes[0]?.id ?? ''
  }
}

export async function loadPreferences(): Promise<void> {
  const values = await api.get<Record<string, string>>('/preferences')
  session.modelKey = values[PREF_MODEL] ?? ''
  session.modeId = values[PREF_MODE] ?? ''
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

export function persistMode(): void {
  void api.put('/preferences', { values: { [PREF_MODE]: session.modeId } })

  // 模式可以绑定一个偏好模型：切过去时跟着换，没配就保持当前选择不动。
  // 偏好模型若已被删掉就跳过 —— 别让选择器指向一个不存在的值
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
      if (event.type === 'text') {
        reply.content += event.text
        void onProgress?.()
      } else if (event.type === 'reasoning') {
        reply.reasoning = (reply.reasoning ?? '') + event.text
      } else if (event.type === 'tool') {
        reply.steps?.push(event.step)
        void onProgress?.()
      } else if (event.type === 'notice') {
        reply.notices?.push(event.text)
      } else if (event.type === 'done') {
        // 以后端落盘的那份为准：字段更全，也和之后从历史里读出来的一致
        Object.assign(reply, event.message)
      }
    }
  } catch (exc) {
    reply.notices?.push(`请求失败：${errorText(exc)}`)
  } finally {
    session.busy = false
    void onProgress?.()
    // 标题和排序时间可能变了，刷一下侧边栏
    void loadConversations()
  }
}

// ---------------------------------------------------------------------------
// 会话操作
// ---------------------------------------------------------------------------
export async function startNewConversation(): Promise<void> {
  const { id } = await api.post<{ id: string }>('/conversations')
  await loadMessages(id)
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
