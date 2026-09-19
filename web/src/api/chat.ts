/**
 * 对话流：把 SSE 响应翻译成一个个事件。
 *
 * 为什么不用浏览器原生的 `EventSource`：它只能发 GET，而发消息要带请求体，
 * 必须 POST。所以这里手写解析 —— 用 fetch 拿到 ReadableStream，按 SSE 的
 * 分隔规则切块。协议本身很简单（`event:` 一行 + `data:` 一行 + 空行结束），
 * 自己解析的代码量比引一个库还少。
 */

import type { ChatEvent, Message, Question, SubagentEvent, ToolStep } from './types'

const BASE = '/api'

export interface ChatPayload {
  conversation_id: string
  prompt: string
  model_config_id: string
  model: string
  mode_id: string
  /** 本轮附件；后端会落盘到工作目录，再把路径告诉模型。 */
  files: File[]
}

/**
 * 发一条消息，逐个产出事件。
 *
 * 用 AsyncGenerator 而不是回调：调用方可以 `for await` 顺序处理，要提前收手也
 * 只需 `break`，不必额外设计一套取消接口。
 *
 * 注意：目前唯一的调用方（ChatView）**没有**实现提前中断 —— 流式输出到一半切走
 * 会话，这个请求会继续跑到结束。要补的话在组件卸载时 break 即可，生成器会就地
 * 停下（这正是用生成器而非回调换来的好处）。
 */
export async function* streamChat(payload: ChatPayload): AsyncGenerator<ChatEvent> {
  // 用 multipart 而不是 JSON：附件得和文本一起上传。拆成「先传文件、再发消息」
  // 两个请求也能做，但那样「文件属于哪一轮」就要靠额外状态去维系。
  //
  // 注意这里不要手写 Content-Type —— 浏览器得自己往里塞 boundary，
  // 手动设置成 multipart/form-data 反而会因为缺少 boundary 而解析失败。
  const body = new FormData()
  body.append('conversation_id', payload.conversation_id)
  body.append('prompt', payload.prompt)
  body.append('model_config_id', payload.model_config_id)
  body.append('model', payload.model)
  body.append('mode_id', payload.mode_id)
  for (const file of payload.files) body.append('files', file)

  const response = await fetch(`${BASE}/chat`, { method: 'POST', body })

  if (!response.ok || !response.body) {
    throw new Error(`对话请求失败（${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // SSE 用空行分隔事件。最后一段可能被切断了，留在 buffer 里等下一块 ——
    // 直接 parse 半截 JSON 会抛错，这就是流式解析最常见的坑
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''

    for (const block of blocks) {
      const event = parseBlock(block)
      if (event) yield event
    }
  }
}

function parseBlock(block: string): ChatEvent | null {
  let name = ''
  let data = ''

  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) name = line.slice(7)
    else if (line.startsWith('data: ')) data += line.slice(6)
  }

  if (!name || !data) return null

  const payload = JSON.parse(data) as Record<string, unknown>

  switch (name) {
    case 'start':
      return { type: 'start', runId: String(payload.run_id ?? '') }
    case 'text':
      return { type: 'text', text: String(payload.text ?? '') }
    case 'reasoning':
      return { type: 'reasoning', text: String(payload.text ?? '') }
    case 'notice':
      return { type: 'notice', text: String(payload.text ?? '') }
    case 'tool':
      return { type: 'tool', step: payload as unknown as ToolStep }
    case 'question':
      return { type: 'question', question: payload as unknown as Question }
    case 'subagent':
      return { type: 'subagent', event: payload as unknown as SubagentEvent }
    case 'done':
      return { type: 'done', message: payload as unknown as Message }
    default:
      return null
  }
}

/**
 * 回答运行中抛出的一个问题。
 *
 * **单独一个普通 POST，不走那条 SSE。** 流的方向始终是「服务端 → 前端」，
 * 中途回传一次用户输入不值得把整条通道换成 WebSocket；而请求-响应天然能重试、
 * 能超时，也不用在流里再定一套反向协议。
 *
 * 返回 false 表示这次回答没被接受 —— 通常是**正常竞态**（点「允许」的同时那一轮
 * 刚好超时结束了）。所以调用方不要把它当错误弹提示。
 */
export async function answerQuestion(
  runId: string,
  questionId: string,
  answer: string,
): Promise<boolean> {
  const response = await fetch(`${BASE}/chat/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, question_id: questionId, answer }),
  })

  if (!response.ok) return false

  const body = (await response.json()) as { accepted?: boolean }
  return body.accepted === true
}

/**
 * 停止一次运行。
 *
 * 注意它只是**请求停止**：接口返回 true 表示标记立起来了，真正结束仍以流里收到
 * `done` 为准。所以调用方不要在这里就把界面切成「空闲」—— 那一轮可能还要跑一小会儿，
 * 提前解锁输入框会让用户以为能发下一条了。
 */
export async function cancelRun(runId: string): Promise<boolean> {
  const response = await fetch(`${BASE}/chat/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  })

  if (!response.ok) return false

  const body = (await response.json()) as { cancelled?: boolean }
  return body.cancelled === true
}
