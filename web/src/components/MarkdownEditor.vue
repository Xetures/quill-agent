<script setup lang="ts">
import { markdown } from '@codemirror/lang-markdown'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { EditorState } from '@codemirror/state'
import { EditorView, placeholder as placeholderExtension } from '@codemirror/view'
import { tags } from '@lezer/highlight'
import { basicSetup } from 'codemirror'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * Markdown 编辑器：CodeMirror 6 的薄包装。
 *
 * 为什么不用 textarea：提示词和技能动辄几百上千字，行号、语法高亮、撤销栈这些
 * 不是锦上添花 —— 没有它们，写一段长提示词很难受。也没选 Monaco：那是 2MB+ 的
 * IDE 级编辑器，为编辑几个 .md 文件不值。
 *
 * 代价要说清：CodeMirror 打出来仍是 600 KB 上下（gzip 约 210 KB）。所以这个组件
 * 由调用方用 `defineAsyncComponent` **异步加载** —— 它成为一个独立 chunk，
 * 只在第一次打开弹窗时下载，两个内容页自己的包一点没变。
 *
 * **配色一律写 `var(--…)`，不写死颜色。** CSS 变量在 CodeMirror 生成的样式里照样
 * 会解析，所以一套代码就够两套皮肤 —— 切主题不用重新创建编辑器。
 */

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    /** 编辑区最小高度。内容更长时编辑器自己长高，由外层容器决定要不要滚。 */
    minHeight?: string
  }>(),
  { placeholder: '', minHeight: '300px' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const host = ref<HTMLElement | null>(null)
let view: EditorView | null = null

/** 语法高亮。只改颜色，排版交给样式表。 */
const highlight = HighlightStyle.define([
  { tag: tags.heading, color: 'var(--text)', fontWeight: '600' },
  { tag: tags.strong, color: 'var(--text)', fontWeight: '600' },
  { tag: [tags.emphasis, tags.quote], color: 'var(--text-soft)', fontStyle: 'italic' },
  { tag: [tags.link, tags.url], color: 'var(--accent)' },
  { tag: [tags.monospace, tags.list], color: 'var(--gold)' },
  { tag: [tags.meta, tags.comment], color: 'var(--text-faint)' },
])

onMounted(() => {
  if (!host.value) return

  view = new EditorView({
    parent: host.value,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        markdown(),
        syntaxHighlighting(highlight),
        placeholderExtension(props.placeholder),
        // 提示词是散文，一行可能很长 —— 不折行就得左右拖着看
        EditorView.lineWrapping,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) emit('update:modelValue', update.state.doc.toString())
        }),
      ],
    }),
  })
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})

/**
 * 外部换值时同步进编辑器（弹窗改编辑另一条记录时会走到这里）。
 *
 * 先比一次再写：不比的话，用户每敲一个字都会绕回来 dispatch 一次，
 * 光标会被顶到文末。
 */
watch(
  () => props.modelValue,
  (value) => {
    if (!view || value === view.state.doc.toString()) return
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
  },
)
</script>

<template>
  <div ref="host" class="editor" :style="{ '--editor-min-height': minHeight }"></div>
</template>

<style scoped>
.editor {
  /* 占满表单行的宽度。不写的话它的宽度由内容决定（CodeMirror 的固有宽度），
   * 在 el-form-item 的 flex 容器里只会缩成一条 */
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

/* 下面全是 CodeMirror 的内部类名 —— 它不提供「主题变量」式的接口，
 * 所以只能覆盖。升级 CodeMirror 后要回归看一眼这几条。 */
.editor :deep(.cm-editor) {
  min-height: var(--editor-min-height);
  background: var(--bg-card);
  color: var(--text);
  font-size: 13px;
}

.editor :deep(.cm-editor.cm-focused) {
  outline: none;
}

.editor :deep(.cm-scroller) {
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  line-height: 1.65;
}

.editor :deep(.cm-gutters) {
  border: none;
  border-right: 1px solid var(--border);
  background: var(--bg-soft);
  color: var(--text-faint);
}

.editor :deep(.cm-activeLine) {
  background: var(--bg-hover);
}

.editor :deep(.cm-activeLineGutter) {
  background: var(--bg-hover);
  color: var(--text-soft);
}

.editor :deep(.cm-cursor),
.editor :deep(.cm-dropCursor) {
  border-left-color: var(--text);
}

.editor :deep(.cm-content ::selection) {
  background: var(--accent-soft);
}

/* drawSelection 画的选择层盖在原生选择之上，得显式覆盖并加 !important */
.editor :deep(.cm-selectionBackground) {
  background: var(--accent-soft) !important;
}

.editor :deep(.cm-placeholder) {
  color: var(--text-faint);
}

/* Ctrl+F 的搜索面板 */
.editor :deep(.cm-panels) {
  background: var(--bg-soft);
  color: var(--text);
}

.editor :deep(.cm-textfield) {
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text);
}
</style>
