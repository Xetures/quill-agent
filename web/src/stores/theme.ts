/**
 * 主题：浅色 / 深色 / 跟随系统。
 *
 * 做法是往 `<html>` 上挂一个 class，颜色全部由 CSS 变量决定 —— 组件里不写任何
 * 「如果是深色就换个色」的判断。好处是加一套主题只要加一组变量，
 * 不用去每个组件里挨个改样式。
 */

import { ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'auto'

const STORAGE_KEY = 'quill:theme'

/** 用户的选择，可能是「跟随系统」。 */
export const themeMode = ref<ThemeMode>(readStored())

/** 实际生效的明暗。auto 时跟随系统，并且会随系统变化。 */
export const isDark = ref(false)

const media = window.matchMedia('(prefers-color-scheme: dark)')

function readStored(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark' || saved === 'auto') return saved
  return 'auto' // 没选过就跟随系统：这是最不需要用户操心的默认
}

export function setTheme(mode: ThemeMode): void {
  themeMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
}

function apply(): void {
  isDark.value = themeMode.value === 'auto' ? media.matches : themeMode.value === 'dark'

  const root = document.documentElement
  // EP 的暗色主题靠 html.dark 生效；再挂一个 data-theme，让自定义 CSS 有个
  // 不依赖 EP 内部实现的判别依据
  root.classList.toggle('dark', isDark.value)
  root.dataset.theme = isDark.value ? 'dark' : 'light'
}

// 系统主题变了，auto 模式下要跟着走
media.addEventListener('change', () => {
  if (themeMode.value === 'auto') apply()
})

// immediate：尽早把 class 挂上。真正防白闪的那一步在 index.html 的内联脚本里
// （那段比任何模块都先执行）
watch(themeMode, apply, { immediate: true })
