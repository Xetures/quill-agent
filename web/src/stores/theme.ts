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
const STORAGE_AUTO_LIGHT = 'quill:auto-light'
const STORAGE_AUTO_DARK = 'quill:auto-dark'

/** 后端偏好里的键名（与 quill_agent.preferences 那套键并存） */
export const PREF_THEME = 'theme'
export const PREF_AUTO_LIGHT = 'auto-light'
export const PREF_AUTO_DARK = 'auto-dark'

const DARK_THEMES = new Set<string>(['dark', 'twilight', 'ember', 'aurora'])
const LIGHT_THEMES = new Set<string>(['light', 'forest', 'amber', 'sakura'])
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

/**
 * 「跟随系统」时深浅各用哪一套。
 *
 * 原先跟随系统固定落在最基础的两档（晴空 / 靛蓝）—— 选了「跟随系统」的人就没法用别的
 * 配色了，而那恰恰是「我懒得手动切」的那批人。现在深浅各记一套，系统一变就换过去。
 */
export const autoLight = ref<ThemeMode>(readStoredAuto(STORAGE_AUTO_LIGHT, LIGHT_THEMES, 'light'))
export const autoDark = ref<ThemeMode>(readStoredAuto(STORAGE_AUTO_DARK, DARK_THEMES, 'dark'))

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

/** 读「跟随系统」时用的那一档：必须是对应明暗里的一个，否则退回默认值。 */
function readStoredAuto(key: string, allowed: Set<string>, fallback: ThemeMode): ThemeMode {
  const saved = localStorage.getItem(key)
  return saved && allowed.has(saved) ? (saved as ThemeMode) : fallback
}

export function setTheme(mode: ThemeMode): void {
  themeMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
}

/**
 * 设定「跟随系统」时深浅各用哪一套。
 *
 * 两个一起设：它们是一对（系统亮时用这个、暗时用那个），分开设没有意义 —— 界面上也是
 * 一个弹窗里同时选两个。
 */
export function setAutoThemes(light: ThemeMode, dark: ThemeMode): void {
  autoLight.value = light
  autoDark.value = dark
  localStorage.setItem(STORAGE_AUTO_LIGHT, light)
  localStorage.setItem(STORAGE_AUTO_DARK, dark)
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
  const resolved =
    themeMode.value === 'auto'
      ? isDark.value
        ? autoDark.value
        : autoLight.value
      : themeMode.value
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

// 深浅那两套：两个值凑一趟请求。immediate 那一次同样是补种。
watch(
  [autoLight, autoDark],
  ([light, dark]) => {
    apply()
    void api
      .put('/preferences', { values: { [PREF_AUTO_LIGHT]: light, [PREF_AUTO_DARK]: dark } })
      .catch(() => {})
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

/** 同上：本地没存过「跟随系统用哪两套」时，采纳后端那份。 */
export function adoptStoredAutoThemes(light: string | undefined, dark: string | undefined): void {
  if (!localStorage.getItem(STORAGE_AUTO_LIGHT) && light && LIGHT_THEMES.has(light)) {
    autoLight.value = light as ThemeMode
  }
  if (!localStorage.getItem(STORAGE_AUTO_DARK) && dark && DARK_THEMES.has(dark)) {
    autoDark.value = dark as ThemeMode
  }
}

/** 供设置页列出「可以选哪几套」。 */
export const LIGHT_THEME_LIST = [...LIGHT_THEMES] as ThemeMode[]
export const DARK_THEME_LIST = [...DARK_THEMES] as ThemeMode[]
