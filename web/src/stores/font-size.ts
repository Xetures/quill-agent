/**
 * 字号：小 / 标准 / 大。
 *
 * 做法和主题完全一致（见 `theme.ts`）：往 `<html>` 上挂一个 data 属性，尺寸全部由
 * CSS 变量决定 —— 组件里不写任何「如果是大字号就……」的判断。将来要加一档，
 * 只是多加一组变量（见 style.css 里字号那一段）。
 *
 * **为什么只动字号、不动间距**：间距承担的是几何 —— 四块玻璃板的缝、桌面壳里让给
 * 系统标题栏的那 28pt。把它们一起放大，观感就不再是「字大了」，而是「整个界面被
 * 拉过一遍」，那些按 pt 对齐系统的地方也会跟着失准。
 *
 * 首屏那一次由 index.html 里的内联脚本负责（比任何模块都先执行），这里管的是
 * 之后的选择与变更。
 *
 * **存两份**（与主题、语言同一套）：localStorage 管首屏那一帧，后端偏好（键
 * `font-size`）管「换了浏览器 / 清了站点数据 / 重装桌面 App 之后还在」。
 */

import { ref, watch } from 'vue'

import { api } from '../api/client'

export type FontSize = 'small' | 'standard' | 'large'

const STORAGE_KEY = 'quill:font-size'

/** 后端偏好里的键名（与 quill_agent.preferences 那套键并存） */
export const PREF_FONT_SIZE = 'font-size'

/** 用户的选择。 */
export const fontSize = ref<FontSize>(readStored())

function readStored(): FontSize {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'small' || saved === 'standard' || saved === 'large') return saved
  // 没选过就是标准：与这套 token 引入之前的样子逐字相同
  return 'standard'
}

export function setFontSize(size: FontSize): void {
  fontSize.value = size
  localStorage.setItem(STORAGE_KEY, size)
}

function apply(): void {
  // 始终写属性（标准档也写）：CSS 那边就不用再处理「没有这个属性」的情况
  document.documentElement.dataset.fontSize = fontSize.value
}

// immediate：模块一加载就把属性挂上，别等用户点进设置页
//
// 顺手同步给后端。immediate 那一次是**补种**：老用户此前只存过 localStorage，后端
// 偏好里还没有，这一趟正好带上。不 await —— 失败不该影响界面。
watch(
  fontSize,
  (size) => {
    apply()
    void api.put('/preferences', { values: { [PREF_FONT_SIZE]: size } }).catch(() => {})
  },
  { immediate: true },
)

/**
 * 本地没存过时采纳后端那份。
 *
 * 场景与主题那边一致：换了浏览器、清了站点数据，此时不该退回标准档，而应该接着用
 * 用户上次选的字号。本地已有值就不动（那是更新的那次选择）。
 */
export function adoptStoredFontSize(value: string | undefined): void {
  if (localStorage.getItem(STORAGE_KEY)) return
  if (value !== 'small' && value !== 'standard' && value !== 'large') return
  fontSize.value = value
}
