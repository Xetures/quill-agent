/**
 * 工具分类的显示名。
 *
 * 后端给的是**稳定 key**（`files` / `shell` / …）而不是中文 —— 分类只用于界面筛选
 * （见 `tools/base.py` 里 `ToolSpec.category` 的说明），所以它属于界面文案，得跟着语言走。
 * MCP 工具的分类是动态的：`mcp:<服务器名>`，这里拼成「MCP · 服务器名」。
 *
 * **顺序也放在这里**，不靠后端的 `sorted()`：按重要性排比按字母排好用，而且换了语言
 * 顺序不会跟着乱（后端返回 key，字母序在中文界面下没有意义）。
 */
import { useI18n } from 'vue-i18n'

/** 展示顺序：常用的在前。MCP 是动态的，排在最后两个内置分类之间（见 `rank`） */
export const CATEGORY_ORDER = [
  'files',
  'shell',
  'code',
  'git',
  'web',
  'skill',
  'subagent',
  'interaction',
  'plan',
  'chat',
  'memory',
  'other',
] as const

const KNOWN = new Set<string>(CATEGORY_ORDER)

/**
 * 排序权重。内置分类听话；`mcp:*` 排在「记忆」之后、「未分类」之前；
 * 认不出的（将来后端加了新分类）一律垫底 —— 垫底而不是丢掉，界面不该悄悄吞东西。
 */
function rank(key: string): number {
  if (key.startsWith('mcp:')) return 10.5
  const index = (CATEGORY_ORDER as readonly string[]).indexOf(key)
  return index === -1 ? 99 : index
}

export function useToolCategory() {
  const { t } = useI18n()

  /** 一个分类 key → 显示名。认不出的原样显示，不吞掉 */
  function label(raw: string): string {
    const value = (raw || '').trim()
    if (!value) return t('toolCategory.other')
    if (value.startsWith('mcp:')) return t('toolCategory.mcp', { server: value.slice(4) })
    return KNOWN.has(value) ? t(`toolCategory.${value}`) : value
  }

  /** 按上面的顺序排一遍。同权重时按 key 比，保证顺序稳定 */
  function sort(keys: string[]): string[] {
    return [...keys].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
  }

  return { label, sort }
}
