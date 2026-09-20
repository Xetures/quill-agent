<script setup lang="ts">
import { computed } from 'vue'

import type { Message, ToolStep } from '../api/types'
import { renderMarkdown } from '../utils/markdown'
import TodoList from './TodoList.vue'

const props = defineProps<{ message: Message }>()

const html = computed(() => renderMarkdown(props.message.content))

// 思考过程也走 computed，和正文一致 —— 原先它是在模板里直接调 renderMarkdown，
// 结果是每次重渲染都会重新解析一遍
const reasoningHtml = computed(() => renderMarkdown(props.message.reasoning ?? ''))

/** 耗时展示：不到 1 秒用毫秒，够长的用秒。两个量级都要看得清。 */
function formatElapsed(seconds: number | undefined): string {
  // 旧记录没有这个字段，按 0 处理 —— 总比显示 "undefineds" 好
  const value = seconds ?? 0
  return value < 1 ? `${Math.round(value * 1000)}ms` : `${value.toFixed(2)}s`
}

/**
 * 折叠块：思考过程一块，每个工具调用各一块。
 *
 * 合成一个数组再渲染，比在模板里写两段 v-for 清楚 —— 尤其是两者都可能为空、
 * 也可能同时存在的时候。
 *
 * 用带判别字段（`kind`）的联合类型，而不是给「思考过程」也塞一个
 * `index: -1` 当哨兵：后者会让读的人去猜 -1 有什么含义，而模板根本不读它。
 */
type Fold =
  | { kind: 'reasoning'; key: string; title: string }
  | { kind: 'step'; key: string; title: string; step: ToolStep }

const folds = computed<Fold[]>(() => {
  const items: Fold[] = []

  if (props.message.reasoning) {
    items.push({ kind: 'reasoning', key: 'reasoning', title: '思考过程' })
  }

  props.message.steps?.forEach((step, index) => {
    items.push({
      kind: 'step',
      key: `step-${index}`,
      title: `🔧 ${step.name}　${formatElapsed(step.elapsed)}`,
      step,
    })
  })

  return items
})

const statsText = computed(() => {
  const stats = props.message.stats
  if (!stats) return ''

  const parts = [`${stats.elapsed.toFixed(1)}s`]
  if (stats.total_tokens) {
    parts.push(
      `${stats.total_tokens} tokens（${stats.prompt_tokens} in / ${stats.completion_tokens} out）`,
    )
  }
  return parts.join(' · ')
})
</script>

<template>
  <div class="message" :class="message.role">
    <!-- 压缩卡片：更早的对话被压成了摘要。
         它是「说明」而不是「替代」—— 原文一条都没删，只是不再发给模型了，
         所以文案必须把这一点讲清楚，否则用户会以为前面的记录丢了。
         默认收起：摘要是给模型看的主线，用户要找我直接翻原文或搜历史 -->
    <el-collapse v-if="message.role === 'summary'" class="folds">
      <el-collapse-item
        name="summary"
        :title="`📦 更早的 ${message.covers ?? 0} 条对话已压缩为摘要（原文仍在会话记录里）`"
      >
        <div class="md reasoning" v-html="html"></div>
      </el-collapse-item>
    </el-collapse>

    <!-- 系统提示不是模型说的话，单独标黄，也不会进入下一轮上下文 -->
    <el-alert
      v-for="(notice, index) in message.notices"
      :key="`notice-${index}`"
      type="warning"
      :closable="false"
      :title="notice"
      show-icon
    />

    <el-collapse v-if="folds.length" class="folds">
      <el-collapse-item v-for="fold in folds" :key="fold.key" :name="fold.key" :title="fold.title">
        <!-- 判别字段是 kind：读代码时不必去推断这个 fold 到底装着什么 -->
        <div v-if="fold.kind === 'reasoning'" class="md reasoning" v-html="reasoningHtml"></div>

        <template v-else>
          <pre class="mono code">{{ fold.step.arguments || '{}' }}</pre>
          <pre class="mono code">{{ fold.step.result }}</pre>
        </template>
      </el-collapse-item>
    </el-collapse>

    <!-- 这一轮的任务清单（跑完落盘的定稿）。排在正文之前，和「思考过程」归为一组：
         它们都是「这条回答背后的过程」，正文才是结论。

         运行中那份不在这里 —— 它走 ChatView 末尾的实时卡片（同一个组件）。
         老记录没有这个字段，所以判空是必须的 -->
    <TodoList v-if="message.todos?.length" :items="message.todos" />

    <!-- 这条消息带的附件。只记了文件名（内容已经落到工作目录），
         所以只做展示，点不开 -->
    <div v-if="message.files?.length" class="files">
      <el-tag v-for="name in message.files" :key="name" size="small" type="info" effect="plain">
        📎 {{ name }}
      </el-tag>
    </div>

    <!-- 摘要的正文已经在上面那张卡片里了，这里跳过 —— 否则同一段显示两遍 -->
    <div
      v-if="message.content && message.role !== 'summary'"
      class="md content"
      v-html="html"
    ></div>

    <span v-if="statsText" class="stats muted">⏱ {{ statsText }}</span>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 用户消息靠右，和模型的左右区分开 */
.message.user {
  align-items: flex-end;
}

.content {
  max-width: 100%;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--bg-soft);
}

.files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 用户消息本来就靠右，附件标签跟着一起靠右 */
.message.user .files {
  justify-content: flex-end;
}

.message.user .content {
  color: var(--on-accent);
  background: var(--accent);
}

.folds {
  --el-collapse-header-height: 34px;
}

/* 折叠块的标题和内容一起右移一格，和正文气泡的内边距（14px）对齐。
 *
 * 不加这一条的话两者都贴着折叠框左边缘 —— 而正文气泡靠自己的 padding 缩进，
 * 于是「思考过程」这个标题连同它的内容都比回答正文靠左 14px，看着像从版心里
 * 掉出去了。**标题也要一起缩**：只缩内容的话，标题反而成了更靠左的那个，
 * 一个块里出现两条不齐的左边线。
 *
 * 需要 :deep()：这两个都是 Element Plus 的内部节点 */
.folds :deep(.el-collapse-item__header),
.folds :deep(.el-collapse-item__content) {
  padding-left: 14px;
  padding-right: 14px;
}

.reasoning {
  color: var(--text-soft);
  /* 思考过程往往很长，且夹着 URL、路径这类不易断行的长串；不设上限会顶破
     折叠框、横向溢出。给个高度上限，超出在块内滚动 */
  max-height: 360px;
  overflow: auto;
  overflow-wrap: anywhere;
}

.code {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--bg-soft);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}

.stats {
  font-size: 12px;
}
</style>
