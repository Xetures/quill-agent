<script setup lang="ts">
import {
  CloseBold,
  Document,
  Loading,
  Plus,
  Promotion,
  WarningFilled,
} from '@element-plus/icons-vue'
import { computed, nextTick, ref, watch } from 'vue'

import ContextMeter from '../components/ContextMeter.vue'
import MessageItem from '../components/MessageItem.vue'
import WorkDirPicker from '../components/WorkDirPicker.vue'
import { renderMarkdown } from '../utils/markdown'
import {
  answerQuestion,
  loadDraft,
  persistMode,
  persistModel,
  saveDraft,
  sendMessage,
  session,
  stopRun,
} from '../stores/session'

// 这里读到的 currentId 多半还是空串 —— bootstrap 在父组件 App 的 onMounted 里
// 才跑，子组件的 setup 比它早。真正加载草稿的是下面那个 watch(currentId)。
// 留着这一行是为了覆盖「刷新页面时 bootstrap 已经跑完」的情况。
const draft = ref(loadDraft(session.currentId))

/** 这一轮要随消息一起发出去的附件。发完就清空 —— 它们已经落到工作目录了。 */
const pending = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

// 模式是必选项：没有模式就没有系统提示词 / 工具 / 技能，那已经不是这个 Agent 了。
// 所以「还没建任何模式」时这里直接禁用发送，而不是悄悄退化成「不用模式」
const canSend = computed(
  () => Boolean(draft.value.trim()) && !session.busy && Boolean(session.modeId),
)

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

/**
 * 模型主动提问时的自由输入（确认题给按钮，用不到它）。
 *
 * 提问卡片的几种形态共用 `answerTo`：选项按钮和输入框最终都是「抛一个字符串回去」。
 */
const freeAnswer = ref('')

/** 审批计划时写在按钮旁边的那句补充说明（驳回时用来说明要改什么）。 */
const planNote = ref('')

const isPlan = computed(() => session.pendingQuestion?.kind === 'plan')

const planHtml = computed(() => renderMarkdown(session.pendingQuestion?.detail ?? ''))

/** 子代理看板只看尾巴。它的完整过程没必要留在屏幕上，那是它自己上下文里的事。 */
const SUBAGENT_TAIL = 6

const recentSubagentEvents = computed(() =>
  session.subagentEvents.slice(-SUBAGENT_TAIL),
)

/** 工具参数是一整块 JSON，压成一行放着；太长就砍掉。 */
function abbreviate(text: string, limit = 88): string {
  const flat = (text ?? '').replace(/\s+/g, ' ')
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat
}

/**
 * 把「按钮 + 补充说明」拼成回传的字符串。
 *
 * 约定：**第一行是按钮文本，其余是补充说明**（后端 `builtin.submit_plan` 按第一个
 * 换行切开）。用换行而不是冒号当分隔符，是因为用户写的说明里完全可能有冒号，
 * 而按钮文本那一行一定是干净的。
 */
function compose(option: string): string {
  const note = planNote.value.trim()
  planNote.value = ''
  return note ? `${option}\n${note}` : option
}

async function answerTo(value: string): Promise<void> {
  freeAnswer.value = ''
  // 清掉残留：计划卡片消失之后那半句说明不该留到下一次提问上
  planNote.value = ''
  await answerQuestion(value)
  void scrollToBottom()
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

        <!-- 子代理正在干活。它的中间过程**不进这条消息**（那正是它存在的意义），
             所以单独开一块把它调了哪些工具播出来 —— 否则一个几十秒的工具调用期间
             界面上什么都没有，看着像卡死了。

             只显示最近几条：这是个「现在在干嘛」的看板，不是日志。成品在它的
             工具步骤里，跑完自己就收掉了。 -->
        <div v-if="recentSubagentEvents.length" class="confirm subagent">
          <div class="confirm-head">
            <el-icon class="confirm-icon spin"><Loading /></el-icon>
            <span>子代理正在工作</span>
          </div>

          <div class="subagent-log">
            <div v-for="(item, index) in recentSubagentEvents" :key="index" class="subagent-line">
              <span v-if="item.type === 'tool'" class="mono">
                → {{ item.name }} {{ abbreviate(item.arguments) }}
              </span>
              <span v-else class="muted">{{ item.text }}</span>
            </div>
          </div>
        </div>

        <!-- 执行前确认。这一轮在服务端**卡在这里等答案**，流不会继续往下走。

             放在消息流末尾而不是弹窗：该不该允许，判断依据就在上面的上下文里
             （模型说它要干什么、之前调过什么），弹窗会把那些盖住。 -->
        <div v-if="session.pendingQuestion" class="confirm">
          <div class="confirm-head">
            <!-- 图标跟着 kind 走：审批计划不是「警告」，用警示图标会让人以为出了事 -->
            <el-icon class="confirm-icon">
              <component :is="isPlan ? Document : WarningFilled" />
            </el-icon>
            <span>{{ session.pendingQuestion.text }}</span>
          </div>

          <!-- 计划用 Markdown 渲染：它是要读的方案（标题、列表、代码块都有），
               塞进等宽 pre 里会糊成一坨。样式复用全局的 .md -->
          <div
            v-if="isPlan && session.pendingQuestion.detail"
            class="md confirm-plan"
            v-html="planHtml"
          />
          <!-- 确认题的 detail 是「要执行的命令」：等宽 + 原样保留换行才看得准 -->
          <pre v-else-if="session.pendingQuestion.detail" class="confirm-detail">{{
            session.pendingQuestion.detail
          }}</pre>

          <!-- 驳回计划时得说清要改什么，否则模型只能猜。批准时也可以补充 -->
          <el-input
            v-if="isPlan"
            v-model="planNote"
            size="small"
            class="confirm-note"
            placeholder="补充说明（驳回时请写清要改什么）"
          />

          <div class="confirm-actions">
            <!-- 有选项就给按钮：选项就那么两三个，点一下比打字快也更不容易出错。
                 第一个是肯定项（后端按 options=(批准, 驳回) 定义），给主色 -->
            <template v-if="session.pendingQuestion.options.length">
              <el-button
                v-for="(option, index) in session.pendingQuestion.options"
                :key="option"
                size="small"
                :type="index === 0 ? 'primary' : 'default'"
                @click="answerTo(compose(option))"
              >
                {{ option }}
              </el-button>
            </template>

            <!-- 模型主动提问时通常没有选项，退回输入框 -->
            <template v-else>
              <el-input
                v-model="freeAnswer"
                size="small"
                class="confirm-input"
                placeholder="输入你的回答"
                @keydown.enter="freeAnswer.trim() && answerTo(freeAnswer)"
              />
              <el-button
                size="small"
                type="primary"
                :disabled="!freeAnswer.trim()"
                @click="answerTo(freeAnswer)"
              >
                回答
              </el-button>
            </template>
          </div>
        </div>

        <el-empty v-if="!session.messages.length" description="在下面输入内容，开始对话" />
      </div>
    </el-scrollbar>

    <div class="composer">
      <!-- 功能选择区：模式 / 模型 / 工作目录 / 附件。
           贴在输入框正上方，和「组成这一轮请求的东西」在同一处。
           模式决定这一轮用哪些提示词、工具、技能和记忆，所以排在第一位 -->
      <div class="tools">
        <el-select
          v-model="session.modeId"
          size="small"
          :placeholder="session.modes.length ? '选择模式' : '还没有模式'"
          class="mode-select"
          @change="persistMode"
        >
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

        <!-- 上下文用量仪表盘：贴在这一行最右侧。它读的是这排里的模型选择（窗口大小），
             放在同一行，改完模型抬头就能看到占比 -->
        <ContextMeter class="meter-slot" />
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
          :placeholder="
            session.modes.length
              ? '输入内容，Enter 发送，Shift + Enter 换行'
              : '请先在「模式」页创建一个模式 —— 没有模式就没有提示词、工具和技能'
          "
          @keydown="onKeydown"
        />
        <!-- 图标按钮融进输入框右下角，比一整块按钮克制。
             跑着的时候，同一个位置换成「停止」—— 那是此刻最想做的动作，
             不该另找个地方放它。用户不用在界面里找第二个按钮 -->
        <el-button
          v-if="session.busy"
          class="send"
          circle
          :icon="CloseBold"
          title="停止这一轮"
          @click="stopRun"
        />
        <el-button
          v-else
          class="send"
          type="primary"
          circle
          :icon="Promotion"
          :disabled="!canSend"
          title="发送"
          @click="send"
        />
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

/* ---------- 执行前确认 ----------
 * 它是「这一轮停在这里等你」，不是一条消息 —— 所以用警示色描边，
 * 在一屏对话里一眼能找到，又不像错误提示那样吓人
 */
.confirm {
  padding: 12px 14px;
  border: 1px solid var(--warn, #c8933f);
  border-radius: 8px;
  background: var(--bg-soft);
}

.confirm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.confirm-icon {
  color: var(--warn, #c8933f);
}

/* 要执行的命令 / 要动的东西。等宽 + 可滚，命令里的空格和换行必须原样保留 */
.confirm-detail {
  margin: 10px 0 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  border-radius: 6px;
  background: var(--bg);
  color: var(--text-soft);
  font-family: var(--mono, monospace);
  font-size: 12px;
  /* pre：保留换行；pre-wrap 让长命令自己折行，不至于横向撑出去 */
  white-space: pre-wrap;
  word-break: break-all;
}

/* 计划正文。它比一条命令长得多，所以给高度上限、超出在卡片内滚动 ——
 * 不然一份详细计划会把输入框顶到屏幕外面去 */
.confirm-plan {
  margin-top: 10px;
  max-height: 340px;
  overflow: auto;
  color: var(--text);
  font-size: 13px;
}

.confirm-note {
  margin-top: 10px;
}

/* ---------- 子代理看板 ---------- */

.subagent {
  /* 它是个「进行中」的提示，比待确认的卡片弱一档：
   * 虚线边框而不是实线，不抢注意力 */
  border-style: dashed;
}

.subagent-log {
  margin-top: 10px;
  max-height: 160px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.7;
}

.subagent-line {
  /* 长参数压一行，超出部分省略；不折行，免得把看板撑成一大块 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-soft);
}

/* 图标转起来 —— 不转的话它看着像个静态装饰，传达不了「还在跑」 */
.spin {
  animation: spin 1.4s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.confirm-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.confirm-input {
  max-width: 320px;
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

/* 仪表盘推到这一行的最右侧：它读的窗口大小就来自左边的模型选择，
 * 两者放同一排，换模型后一眼能看到占比跟着变 */
.meter-slot {
  flex-shrink: 0;
  margin-left: auto;
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
