/**
 * 界面语言。
 *
 * **当前是骨架阶段**：基础设施 + 侧边栏导航 + 偏好设置（含本页自己）已经接入，
 * 其余页面仍是中文硬编码，正在分批迁移 —— 所以英文那一档的说明里如实写了
 * 「部分位置仍是中文」。宁可说清楚，也不要让人切过去以后怀疑切换没生效。
 *
 * **存在 localStorage**（与主题、字号一致）：首屏要立刻用上，等不了后端那一趟往返；
 * 首屏那次由 index.html 的内联脚本负责。
 *
 * 同时往**后端偏好**里写一份（键名 `language`）：后端也有要显示给用户的文案
 * （工具分类这类），将来它得知道自己该用哪种语言。前端是选择的地方，后端是使用的地方。
 */
import { createI18n } from 'vue-i18n'
import { ref, watch } from 'vue'

import { api } from '../api/client'
import enUS from './en-US'
import zhCN from './zh-CN'

export const LOCALES = ['zh-CN', 'en-US'] as const
export type Locale = (typeof LOCALES)[number]

const STORAGE_KEY = 'quill:locale'
const FALLBACK: Locale = 'zh-CN'

/** 后端偏好里的键名（与 quill_agent.preferences 那套键并存） */
export const PREF_LANGUAGE = 'language'

export const locale = ref<Locale>(readStored())

function readStored(): Locale {
  // 只有明确存过 en-US 才算英文：没选过、写坏了都回中文（默认语言）
  return localStorage.getItem(STORAGE_KEY) === 'en-US' ? 'en-US' : FALLBACK
}

export const i18n = createI18n({
  legacy: false,
  // 模板里直接写 $t('…')，不必每个组件都 useI18n() —— 这个应用没有
  // 「同页面多种语言」的需求，少一层样板。脚本里要用 `t` 仍然得自己引入
  globalInjection: true,
  // 少数文案里**有意**带行内标签（`<code>` / `<strong>` / `<kbd>`），由组件用 v-html 渲染。
  // 不开这个开关的话，每条这样的文案都会在控制台刷一条安全提醒 —— 提醒本身没错
  // （HTML 消息确实有 XSS 风险），但这些字符串是我们自己写的，不含任何用户输入
  warnHtmlMessage: false,
  locale: locale.value,
  fallbackLocale: FALLBACK,
  messages: { 'zh-CN': zhCN, 'en-US': enUS },
})

export function setLocale(value: Locale): void {
  locale.value = value
}

// immediate：模块一加载就跟上（首屏那次由内联脚本负责，这里管之后的一切）
watch(
  locale,
  (value) => {
    i18n.global.locale.value = value
    localStorage.setItem(STORAGE_KEY, value)
    document.documentElement.lang = value
    // 顺手同步给后端。不 await：偏好是「记一下」，失败了不该影响界面
    void api.put('/preferences', { values: { [PREF_LANGUAGE]: value } }).catch(() => {})
  },
  { immediate: true },
)

/**
 * 本地没存过时采纳后端那份。
 *
 * 场景：换了浏览器或清了站点数据，但后端偏好还在 —— 那时不该退回中文，而应该接着用
 * 用户上次选的语言。本地已有值就不动（那是更新的那次选择）。
 */
export function adoptStoredLocale(value: string | undefined): void {
  if (localStorage.getItem(STORAGE_KEY)) return
  if (value !== 'zh-CN' && value !== 'en-US') return
  locale.value = value
}
