/**
 * 公共备忘录：一块**自己写**的便签，跨页面、跨会话都在。
 *
 * 和公共剪贴板是一对，方向相反：
 *
 *     剪贴板  被动收集「应用内复制过的东西」，左边没有它，入口在右侧
 *     备忘录  主动记录「你自己敲下来的东西」，入口在左侧
 *
 * 所以它**没有 `push` 这类入口** —— 正文只有一个来源：用户打字。相应地，存盘时机也
 * 不一样：剪贴板是每次复制写一次，这里是输入时按节流写（见 MemoPanel）。
 *
 * 落 localStorage：和剪贴板同理，刷新之后还在；这是本机应用，内容本来就只在这台机器上。
 */

import { reactive } from 'vue'

const STORAGE_KEY = 'quill:memo'

function load(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export const memo = reactive({
  /** 正文。Markdown，原样保存，不做任何解析。 */
  text: load(),
  /** 左侧面板开着没有。 */
  open: false,
})

/** 存盘。输入时高频调用，节流在调用方（见 MemoPanel）。 */
export function persistMemo(): void {
  try {
    localStorage.setItem(STORAGE_KEY, memo.text)
  } catch {
    // 存不下（隐私模式、配额满）就算了：它是便利，不是数据 —— 不该因此报错
  }
}

export function clearMemo(): void {
  memo.text = ''
  persistMemo()
}
