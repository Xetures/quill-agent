<script setup lang="ts">
import { CopyDocument, Delete } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { api } from '../api/client'
import type { FileChange, Message, ToolStep } from '../api/types'
import { session } from '../stores/session'
import { errorText } from '../utils/error'
import { renderMarkdown } from '../utils/markdown'
import TodoList from './TodoList.vue'

const props = defineProps<{
  message: Message
  /**
   * 这条消息在会话里的位置。
   *
   * **可选**，而且只有落盘的消息才有：正在生成的那条不在服务端的历史里，给它传索引
   * 只会指向别的一条。没有它 → 不显示操作按钮（见 `deletable`）。
   */
  index?: number
}>()

const emit = defineEmits<{ deleted: [] }>()

/** 还原中，防连点。 */
const restoring = ref(false)

/**
 * 能不能删。
 *
 * 两个条件：**已经落盘**（有索引），而且不是摘要 —— 摘要是压缩的产物，删掉它，
 * 被它覆盖的那些原文下一轮就会被重新塞回上下文，等于什么都没做。
 */
const deletable = computed(
  () => props.index !== undefined && props.message.role !== 'summary',
)

/**
 * 复制这条消息的正文。
 *
 * 正文为空时退回思考过程：只有工具调用的那一轮，正文本来就是空的，而思维链才是
 * 用户想留下的东西（见 7.x 的「思考」开关）。
 */
async function copyMessage(): Promise<void> {
  const text = props.message.content || props.message.reasoning || ''
  if (!text.trim()) {
    ElMessage.info('这条消息没有可以复制的文本。')
    return
  }

  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    // 剪贴板 API 需要安全上下文（https 或 localhost）；被拒时别装作成功
    ElMessage.error('复制失败：浏览器拒绝了剪贴板访问。')
  }
}

/** 删除这条消息（真删，服务端不再把它组进后续上下文）。 */
async function deleteMessage(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      // 说清后果：只讲「删除」的话，用户不知道删掉之后模型还记不记得
      '删除这条消息？删除后它不再参与后续对话 —— 模型下一轮就看不到它了。',
      '删除消息',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户按了取消
  }

  try {
    await api.del(
      `/conversations/${encodeURIComponent(session.currentId)}/messages/${props.index}`,
    )
    ElMessage.success('已删除')
    // 让父组件重新拉一遍消息：删掉一条之后，后面每条的索引都往前挪了一位，
    // 就地改本地数组很容易漏掉这一点
    emit('deleted')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
}

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
      title: `🔧 ${step.name}　${formatElapsed(step.elapsed)}${stepStat(step)}`,
      step,
    })
  })

  return items
})

/**
 * 折叠标题里的改动统计（`+12 −3`）。
 *
 * 放在标题上而不是只放折叠里：**扫一眼就知道改了多少**是这件事的主要价值，
 * 想看具体改了哪几行再展开。
 */
function stepStat(step: ToolStep): string {
  const changes = step.changes ?? []
  const added = changes.reduce((sum, item) => sum + item.added, 0)
  const removed = changes.reduce((sum, item) => sum + item.removed, 0)
  return added || removed ? `　+${added} −${removed}` : ''
}

/** diff 的一行，已判定好该用哪种样式。 */
type DiffLine = { key: string; cls: string; text: string }

/**
 * 把这次调用的文件改动摊成一行行，交给模板按行上色。
 *
 * 之所以在后端算 diff、前端只负责上色：算 diff 要知道改动前的原文，那份内容只在后端有。
 * 前端拿到的就是一段 unified diff 文本，按行首字符判类型足够准。
 */
function diffLines(changes: FileChange[] | undefined): DiffLine[] {
  const out: DiffLine[] = []

  for (const change of changes ?? []) {
    out.push({
      key: `${change.path}#head`,
      cls: 'head',
      text: `── ${change.path}　+${change.added} −${change.removed}`,
    })

    if (change.binary) {
      out.push({ key: `${change.path}#bin`, cls: 'ctx', text: '（二进制或文件过大，内容未记录）' })
      continue
    }

    const lines = change.diff ? change.diff.split('\n') : []
    if (!lines.length) {
      // 新建文件没有 diff（见后端）：那份「改动」就是全文，全绿的一片没有信息量
      out.push({ key: `${change.path}#new`, cls: 'ctx', text: `（新建 ${change.added} 行）` })
      continue
    }

    lines.forEach((text, index) => {
      // `---` / `+++` 是 diff 的文件头。上面已经有 `── 路径` 那一行了，
      // 再显示一次只是两条没有信息的噪音
      if (text.startsWith('---') || text.startsWith('+++')) return

      const cls = text.startsWith('@@')
        ? 'hunk'
        : text.startsWith('+')
          ? 'add'
          : text.startsWith('-')
            ? 'del'
            : 'ctx'
      out.push({ key: `${change.path}#${index}`, cls, text })
    })
  }

  return out
}

/**
 * 还原过之后把这一轮的入口收掉。
 *
 * 用一个本地标记而不是去改 `message`：`props` 是只读的，改了要么报错、要么埋一个
 * 只在运行时才炸的坑。而且还原是**这次界面会话**里发生的动作，本来也不必写回会话记录。
 */
const restoredAlready = ref(false)

/** 这一轮改了哪几个文件（消息下面那行、「还原」按钮都看它）。 */
const changedFiles = computed(() =>
  restoredAlready.value ? [] : (props.message.changes?.files ?? []),
)

/**
 * 把这一轮改动过的文件还原到改动前。
 *
 * 确认框里必须写明**命令的副作用不在范围内**：模型跑了 `sed -i`、`mv`、`git checkout`
 * 改掉的东西，我们追踪不到 —— 不说的话，用户会以为「还原」等于「什么都没发生」。
 */
async function restore(): Promise<void> {
  const runId = props.message.changes?.run_id
  if (!runId || restoring.value) return

  try {
    await ElMessageBox.confirm(
      `把这一轮改动过的 ${changedFiles.value.length} 个文件还原到改动前？` +
        // 不用 Markdown 记号的星号：确认框是纯文本，星号会原样显示出来
        '\n\n注意：这一轮「执行过的命令」造成的改动不会还原（比如 mv、sed -i、git checkout）。',
      '还原改动',
      { type: 'warning', confirmButtonText: '还原', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户按了取消
  }

  restoring.value = true
  try {
    const result = await api.post<{ restored: string[]; skipped: string[] }>(
      `/conversations/${encodeURIComponent(session.currentId)}/changes/restore`,
      { run_id: runId },
    )

    ElMessage.success(
      result.skipped.length
        ? `已还原 ${result.restored.length} 个文件；${result.skipped.length} 个没能还原（二进制或过大）`
        : `已还原 ${result.restored.length} 个文件`,
    )

    // 那份改动已经不存在了，把入口收掉
    restoredAlready.value = true
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    restoring.value = false
  }
}

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
          <!-- 这次调用改了什么。排在参数/结果**前面** —— 用户最想看的是改动本身，
               而不是我们发给模型的参数长什么样 -->
          <pre v-if="fold.step.changes?.length" class="diff mono"><span
              v-for="line in diffLines(fold.step.changes)"
              :key="line.key"
              :class="line.cls"
            >{{ line.text }}</span></pre>

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

    <!-- 这一轮改了哪些文件 + 还原入口。
         挂在消息上而不是工具步骤里：还原是**按轮次**的（一整轮的文件一起回去），
         而工具步骤是「这一次调用」的粒度 -->
    <div v-if="changedFiles.length" class="changes">
      <span class="muted">
        ✎ 改了 {{ changedFiles.length }} 个文件：{{ changedFiles.map((item) => item.path).join('、') }}
      </span>
      <el-button
        size="small"
        text
        type="warning"
        :loading="restoring"
        @click="restore"
      >
        还原
      </el-button>
    </div>

    <!-- 气泡下面那一行：左边是耗时与用量（模型消息才有），右边是删除 / 复制。
         放在这里而不是气泡右上角：那一行本来就是「这条消息的元信息」，按钮和它同一行
         读起来是一件事，而浮在正文右上角会挡住内容 -->
    <div v-if="statsText || deletable" class="footer">
      <span v-if="statsText" class="stats muted">⏱ {{ statsText }}</span>

      <!-- **常驻但很淡**：只 hover 才露出来的按钮用户很难发现，而全亮又抢正文的注意力。
           悬停只改透明度、不加底色 —— 加底色会让它在消息框里显得像个突然冒出来的控件 -->
      <div v-if="deletable" class="actions">
        <el-button text size="small" :icon="Delete" title="删除" @click="deleteMessage" />
        <el-button text size="small" :icon="CopyDocument" title="复制" @click="copyMessage" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ---------- 气泡下面那一行：耗时 / 用量 + 操作按钮 ---------- */

.footer {
  display: flex;
  align-items: center;
  gap: 10px;
  /* 必须撑满：`.message.user` 那边是 align-items: flex-end，不给宽度的话这一行只有
   * 内容那么宽，下面的 `margin-left: auto` 就推不动，按钮会贴在状态字旁边 */
  width: 100%;
}

/* 常驻但很淡，悬停只改透明度。
 * **不加底色、不加边框**：那会让两个图标在消息流里显得像突然冒出来的控件，很扎眼 */
.actions {
  /* 推到最右：左边是「这条消息的元信息」，右边是操作 */
  margin-left: auto;
  display: flex;
  gap: 2px;
  opacity: 0.35;
  transition: opacity 0.15s ease;
}

.message:hover .actions {
  opacity: 1;
}

/* 压掉 Element 按钮自带的底色。不压的话，悬停会出现一块方块背景 ——
 * 和「只是两个淡图标」的意图正相反 */
.actions :deep(.el-button) {
  --el-button-text-color: var(--text-soft);
  --el-button-hover-text-color: var(--el-color-primary);
  --el-button-hover-bg-color: transparent;
  --el-button-active-bg-color: transparent;
  padding: 0 2px;
  height: auto;
}

/* ---------- 改动（diff）---------- */

/* 一行一个 span，而不是靠换行符：diff 的每一行要独立上色，而 <pre> 里的换行符
 * 没法单独包一层。display: block 让它各自占一行，同时保留 pre 的空白语义 */
.diff {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  overflow-x: auto;
}

.diff span {
  display: block;
  /* 负的左右留白：背景色铺到 pre 的边缘，看着像一整条，而不是一行行碎块 */
  padding: 0 6px;
  margin: 0 -6px;
}

.diff .head {
  color: var(--text-soft);
  font-weight: 600;
}

.diff .hunk {
  color: var(--text-soft);
}

.diff .add {
  color: #1b7f3b;
  background: rgb(76 175 80 / 14%);
}

.diff .del {
  color: #b3261e;
  background: rgb(224 49 49 / 12%);
}

/* 这一轮的改动汇总 + 还原 */
.changes {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
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
