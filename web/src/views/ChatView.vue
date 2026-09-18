<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, nextTick, ref, watch } from 'vue'

import MessageItem from '../components/MessageItem.vue'
import WorkDirPicker from '../components/WorkDirPicker.vue'
import {
  loadDraft,
  persistMode,
  persistModel,
  saveDraft,
  sendMessage,
  session,
} from '../stores/session'

// 这里读到的 currentId 多半还是空串 —— bootstrap 在父组件 App 的 onMounted 里
// 才跑，子组件的 setup 比它早。真正加载草稿的是下面那个 watch(currentId)。
// 留着这一行是为了覆盖「刷新页面时 bootstrap 已经跑完」的情况。
const draft = ref(loadDraft(session.currentId))

/** 这一轮要随消息一起发出去的附件。发完就清空 —— 它们已经落到工作目录了。 */
const pending = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const canSend = computed(() => Boolean(draft.value.trim()) && !session.busy)

/**
 * 顶栏显示的会话标题。
 *
 * 会话还没起名时（刚建的空会话）不显示 —— 顶栏只留页面名，比显示一个
 * 「新会话」占位符干净。
 */
const currentTitle = computed(
  () => session.conversations.find((item) => item.id === session.currentId)?.title ?? '',
)

/** 只用到 setScrollTop，用结构化类型标注就够了，不必去引组件实例的类型 */
const scrollBox = ref<{ setScrollTop: (value: number) => void } | null>(null)

// 换会话就换草稿：每个会话各写各的，互不覆盖
watch(
  () => session.currentId,
  (id) => {
    draft.value = loadDraft(id)
    void scrollToBottom()
  },
)

// 输入即存本地。localStorage 是同步的、不涉及网络，敲字不会有任何延迟
watch(draft, (text) => saveDraft(session.currentId, text))

async function scrollToBottom(): Promise<void> {
  await nextTick()
  // 传一个足够大的值，滚动条内部会自己夹到实际最大位置
  scrollBox.value?.setScrollTop(Number.MAX_SAFE_INTEGER)
}

function pickFiles(): void {
  fileInput.value?.click()
}

function onFilesPicked(event: Event): void {
  const input = event.target as HTMLInputElement
  pending.value.push(...Array.from(input.files ?? []))

  // 清空 input 的值：不清的话，连着选同一个文件不会再触发 change 事件
  input.value = ''
}

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || session.busy) return

  draft.value = ''

  // 附件交出去就清空列表：它们会随这一轮请求上传、落到工作目录，
  // 留着只会让下一条消息莫名其妙地又带一遍
  const files = pending.value
  pending.value = []

  // 组装的活全在 store 里（见 sendMessage）——这里只管输入框和滚动
  await sendMessage({ prompt: text, files, onProgress: scrollToBottom })
}

function onKeydown(event: Event | KeyboardEvent): void {
  // el-input 把原生事件原样透传出来，类型上可能是通用 Event —— 先收敛再判断
  if (!(event instanceof KeyboardEvent)) return

  // Enter 发送、Shift+Enter 换行；isComposing 用来避开中文输入法的选词回车 ——
  // 少了这个判断，用拼音打字时一选词就把消息发出去了
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  }
}
</script>

<template>
  <div class="chat">
    <!-- 投递到顶栏。Teleport 不产生实际节点，所以不影响下面 .chat 的 flex 布局 -->
    <Teleport to="#page-head-slot">
      <h1>任务</h1>
      <span v-if="currentTitle" class="hint">{{ currentTitle }}</span>
    </Teleport>

    <el-scrollbar ref="scrollBox" class="stream">
      <div class="stream-inner">
        <MessageItem v-for="(message, index) in session.messages" :key="index" :message="message" />
        <el-empty v-if="!session.messages.length" description="在下面输入内容，开始对话" />
      </div>
    </el-scrollbar>

    <div class="composer">
      <!-- 功能选择区：模式 / 模型 / 工作目录 / 附件。
           贴在输入框正上方，和「组成这一轮请求的东西」在同一处 -->
      <div class="tools">
        <el-select
          v-model="session.modeId"
          size="small"
          placeholder="提示词模式"
          class="mode-select"
          @change="persistMode"
        >
          <el-option label="（不用模式）" value="" />
          <el-option
            v-for="mode in session.modes"
            :key="mode.id"
            :label="mode.name"
            :value="mode.id"
          />
        </el-select>

        <el-select
          v-model="session.modelKey"
          size="small"
          placeholder="选择模型"
          class="model-select"
          @change="persistModel"
        >
          <el-option
            v-for="item in session.models"
            :key="item.key"
            :label="item.label"
            :value="item.key"
          />
        </el-select>

        <WorkDirPicker />

        <el-button size="small" :icon="Plus" title="上传文件" @click="pickFiles">附件</el-button>

        <!-- 原生 file input 藏起来，由上面的按钮代为触发：
             el-upload 会自己维护一套文件列表，而这里的列表已经由 pending 管着 -->
        <input ref="fileInput" type="file" multiple hidden @change="onFilesPicked" />
      </div>

      <div v-if="pending.length" class="pending">
        <el-tag
          v-for="(file, index) in pending"
          :key="index"
          size="small"
          closable
          @close="pending.splice(index, 1)"
        >
          {{ file.name }}
        </el-tag>
      </div>

      <div class="box">
        <el-input
          v-model="draft"
          type="textarea"
          resize="none"
          :rows="3"
          :disabled="session.busy"
          placeholder="输入内容，Enter 发送，Shift + Enter 换行"
          @keydown="onKeydown"
        />
        <el-button
          class="send"
          type="primary"
          size="small"
          :loading="session.busy"
          :disabled="!canSend"
          @click="send"
        >
          {{ session.busy ? '生成中' : '发送' }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  /* 用 flex: 1 而不是 height: 100%：.main 里可能还挂着一条启动错误提示，
   * 高度写死成 100% 就会被它顶出去（同样是「看不见输入框」的成因之一） */
  flex: 1;
  min-height: 0;
}

.stream {
  flex: 1;
  min-height: 0;
}

.stream-inner {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 20px;
}

.composer {
  /* 输入区在任何窗口高度下都要留在视口里，所以不被压缩 */
  flex-shrink: 0;
  padding: 10px 16px 16px;
  border-top: 1px solid var(--border);
}

.tools {
  display: flex;
  align-items: center;
  gap: 8px;
  /* 窗口压窄时换行，而不是把工作目录挤没 */
  flex-wrap: wrap;
}

.mode-select {
  flex-shrink: 0;
  width: 150px;
}

.model-select {
  flex-shrink: 0;
  width: 220px;
}

.pending {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.box {
  position: relative;
  margin-top: 8px;
}

/* 底部留出一块空白专门给发送按钮：文字最多 3 行，第 4 行的高度正好让按钮待着，
 * 两者不会重叠。这是「按钮在输入框里面」最省事的实现 —— el-input 没有放按钮的插槽 */
.box :deep(.el-textarea__inner) {
  padding: 8px 12px 42px;
}

.send {
  position: absolute;
  right: 8px;
  bottom: 8px;
}
</style>
