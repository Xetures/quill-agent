/**
 * 公共剪贴板：应用内复制过的内容都会落到这里，跨页面、跨会话可见。
 *
 * 为什么在系统剪贴板之外再留一份：用户经常要把一段内容「拿到别处用」——
 * 把模型的回答贴进提示词页、把一段配置贴进连接表单。系统剪贴板一次只装得下一份，
 * 复制第二段就把第一段挤掉了，回头要用的东西还得再翻回去找。
 *
 * 落 localStorage：刷新之后还在。这是本机应用，内容本来就只存在这台机器上。
 */

import { reactive } from 'vue'

export interface ClipItem {
  id: string
  /** 复制的原文。 */
  text: string
  /** 从哪儿复制的（「助手回答」「我的提问」…），列表里用来辨认。 */
  from: string
  /** 复制时间，毫秒时间戳。 */
  at: number
}

/** 留最近多少条。再多也没人翻，白占 localStorage。 */
const MAX_ITEMS = 50
const STORAGE_KEY = 'quill:clipboard'

function load(): ClipItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // 逐条过一遍：存下来的东西可能是旧版本写的，字段不全就丢掉，别让界面崩
    return parsed
      .filter(
        (item): item is ClipItem =>
          Boolean(item) &&
          typeof (item as ClipItem).text === 'string' &&
          typeof (item as ClipItem).id === 'string',
      )
      .slice(0, MAX_ITEMS)
  } catch {
    return []
  }
}

export const clipboard = reactive({
  /** 最新的在最前面（看到的顺序就是复制顺序的倒序）。 */
  items: load() as ClipItem[],
  /** 侧边面板开着没有。 */
  open: false,
})

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clipboard.items))
  } catch {
    // 存不下（隐私模式、配额满）就算了：这一份是便利，不是数据 —— 不该因此报错
  }
}

/**
 * 记一条复制。**所有应用内的复制入口都应该调它**，否则用户会疑惑
 * 「我刚复制的怎么没进去」。
 */
export function pushClipboard(text: string, from = ''): void {
  const value = text.trim()
  if (!value) return

  // 连着复制同一段（比如点两下按钮）不重复记：列表里堆两行一样的东西没有意义
  if (clipboard.items[0]?.text === value) return

  clipboard.items.unshift({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    text: value,
    from,
    at: Date.now(),
  })
  if (clipboard.items.length > MAX_ITEMS) clipboard.items.length = MAX_ITEMS
  persist()
}

export function removeClipboardItem(id: string): void {
  const index = clipboard.items.findIndex((item) => item.id === id)
  if (index >= 0) clipboard.items.splice(index, 1)
  persist()
}

export function clearClipboard(): void {
  clipboard.items = []
  persist()
}
