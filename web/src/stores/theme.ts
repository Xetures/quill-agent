/**
 * 主题：浅色 / 深色 / 跟随系统。
 *
 * 做法是往 `<html>` 上挂一个 class，颜色全部由 CSS 变量决定 —— 组件里不写任何
 * 「如果是深色就换个色」的判断。好处是加一套主题只要加一组变量，
 * 不用去每个组件里挨个改样式。
 *
 * **存两份，分工不同**（与语言、字号一致，见 `locales/index.ts`）：
 *   - `localStorage`：首屏那一帧就要用，等不了后端那一趟往返；由 index.html 里的内联
 *     脚本读（那段比任何模块都先执行）。
 *   - **后端偏好**（键 `theme`）：跟着数据根走。换了浏览器、清了站点数据、重装了桌面
 *     App —— 那些时候 localStorage 没了，而偏好还在。桌面壳的 webview 与浏览器各有各
 *     的 localStorage，靠偏好这一份才两边一致。
 */

import { ref, watch } from 'vue'

import { api } from '../api/client'

export type ThemeMode =
  | 'light'
  | 'forest'
  | 'amber'
  | 'sakura'
  | 'dark'
  | 'twilight'
  | 'ember'
  | 'aurora'
  | 'auto'

const STORAGE_KEY = 'quill:theme'

/** 后端偏好里的键名（与 quill_agent.preferences 那套键并存） */
export const PREF_THEME = 'theme'

const DARK_THEMES = new Set<string>(['dark', 'twilight', 'ember', 'aurora'])
const VALID_THEMES = new Set<string>([
  'light',
  'forest',
  'amber',
  'sakura',
  'dark',
  'twilight',
  'ember',
  'aurora',
  'auto',
])

/** 用户的选择，可能是「跟随系统」或指定主题。 */
export const themeMode = ref<ThemeMode>(readStored())

/** 实际生效的明暗。auto 时跟随系统，并且会随系统变化。 */
export const isDark = ref(false)

const media = window.matchMedia('(prefers-color-scheme: dark)')

function readStored(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved && VALID_THEMES.has(saved)) {
    return saved as ThemeMode
  }
  return 'auto' // 没选过就跟随系统：这是最不需要用户操心的默认
}

export function setTheme(mode: ThemeMode): void {
  themeMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
}

function apply(): void {
  if (themeMode.value === 'auto') {
    isDark.value = media.matches
  } else {
    isDark.value = DARK_THEMES.has(themeMode.value)
  }

  const root = document.documentElement
  // EP 的暗色主题靠 html.dark 生效；再挂一个 data-theme，让自定义 CSS 有精确的判别依据
  root.classList.toggle('dark', isDark.value)
  const resolved = themeMode.value === 'auto' ? (isDark.value ? 'dark' : 'light') : themeMode.value
  root.dataset.theme = resolved
}

// 系统主题变了，auto 模式下要跟着走
media.addEventListener('change', () => {
  if (themeMode.value === 'auto') apply()
})

// immediate：尽早把 class 挂上。真正防白闪的那一步在 index.html 的内联脚本里
// （那段比任何模块都先执行）
//
// 顺手同步给后端。immediate 那一次是**补种**：老用户在这儿之前只存过 localStorage，
// 后端偏好里还没有，这一趟正好把它带上。不 await —— 偏好是「记一下」，
// 失败了不该影响界面。
watch(
  themeMode,
  (mode) => {
    apply()
    void api.put('/preferences', { values: { [PREF_THEME]: mode } }).catch(() => {})
  },
  { immediate: true },
)

/**
 * 本地没存过时采纳后端那份。
 *
 * 场景：换了浏览器、清了站点数据 —— 那时不该退回「跟随系统」，而应该接着用用户上次
 * 选的主题。本地已有值就不动（那是更新的那次选择）。
 */
export function adoptStoredTheme(value: string | undefined): void {
  if (localStorage.getItem(STORAGE_KEY)) return
  if (!value || !VALID_THEMES.has(value)) return
  themeMode.value = value as ThemeMode
}
