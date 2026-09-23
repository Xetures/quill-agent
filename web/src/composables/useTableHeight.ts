import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/**
 * 量出容器高度，交给 `el-table` 的 `:height` —— 让**表格自己滚**（表头固定、
 * 只有表体在滚）。
 *
 * 为什么不套一层 `el-scrollbar`：那个方案下滚动条长在外面，表头会跟着内容一起
 * 滚走；更要紧的是 el-table 内部的 `.el-table__body-wrapper` 是 `overflow: hidden`
 * 且高度等于全部内容 —— 鼠标停在表格上滚轮时，浏览器找不到可滚的祖先，
 * 表现就是「列表滚不动」（用户反馈）。
 *
 * 用量页一直是这么做的（`UsageView`），这里抽出来给其余几个列表页共用。
 */
export function useTableHeight(box: Ref<HTMLElement | null>, min = 140): Ref<number> {
  const height = ref(320)
  let observer: ResizeObserver | null = null

  onMounted(() => {
    if (!box.value) return

    observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.height ?? 0
      // 下限：窗口很矮时也别压成一条缝，否则表头都露不全
      if (measured) height.value = Math.max(min, Math.round(measured))
    })
    observer.observe(box.value)
  })

  onBeforeUnmount(() => observer?.disconnect())

  return height
}
