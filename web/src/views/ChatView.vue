<script setup lang="ts">
import {
  CloseBold,
  Document,
  Loading,
  Plus,
  Promotion,
  WarningFilled,
} from '@element-plus/icons-vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import ContextMeter from '../components/ContextMeter.vue'
import MessageItem from '../components/MessageItem.vue'
import TodoList from '../components/TodoList.vue'
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

/* ---------- 跟随最新内容 ---------- */

/**
 * 消息内容区。它的高度一变就说明「有新内容」——跟随滚动盯的是它，不是事件。
 */
const streamInner = ref<HTMLElement | null>(null)

/**
 * 浮层（子代理看板 + 待确认卡片）。
 *
 * 它盖在输出区之上、**不占输出区的高度** —— 所以消息区底部得主动留出等高的空间，
 * 否则滚到底时最后一条消息会被永久压在浮层下面（那和「看不见」没区别）。
 */
const floaterEl = ref<HTMLElement | null>(null)

/** 消息区底部为浮层留出的空间：浮层高度 + 一点视觉间距。 */
const floaterPad = ref(16)

/**
 * 滚动容器（el-scrollbar 的 __wrap），挂载时从内容区往上找一次。
 *
 * 不去拿 el-scrollbar 的组件实例：这里只需要「读 scrollHeight」和「写 scrollTop」
 * 两件事，用 DOM 反而更稳 —— 实例上有没有暴露 `setScrollTop` 取决于组件版本，
 * 拿不到就会静默地不滚（而这种失败没有任何提示）。
 */
let wrapEl: HTMLElement | null = null

/**
 * 是否跟随。
 *
 * 用户往上翻看历史时不再把他拽回底部 —— 一轮长任务里「边跑边回看」和「跟着看
 * 最新一行」一样常用，硬跟等于把滚动条从他手里抢走。所以只在本来就贴着底部时
 * 才跟着走；翻上去就停住，自己滚回来又继续跟。
 */
let follow = true

/** 离底部多远之内算「还贴着底部」。留点余量：滚轮和触摸板很难停在精确的最后 1px */
const FOLLOW_SLACK = 120

let observer: ResizeObserver | null = null

function atBottom(): boolean {
  if (!wrapEl) return true
  return wrapEl.scrollHeight - wrapEl.scrollTop - wrapEl.clientHeight <= FOLLOW_SLACK
}

/** 我们自己设 scrollTop 也会走到这里，但那时离底部的距离是 0，结论一样 */
function onStreamScroll(): void {
  follow = atBottom()
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (!wrapEl) return
  // 直接写到底：超过最大值时浏览器会自己夹到最大位置
  wrapEl.scrollTop = wrapEl.scrollHeight
  follow = true
}

onMounted(() => {
  const inner = streamInner.value
  if (!inner) return

  wrapEl = inner.closest<HTMLElement>('.el-scrollbar__wrap')
  wrapEl?.addEventListener('scroll', onStreamScroll, { passive: true })

  // 「有新内容」用高度变化判定，而不是挂在几个已知的事件上：事件驱动必漏。
  // 一轮结束时的 done 会把整条消息换成后端那份（正文、工具步骤、统计一起换），
  // Markdown 重排和代码块渲染也可能比最后一个文本增量晚 —— 于是「流早就停了，
  // 最后一段却还在折叠线下面，得手动滚一下」。盯住内容区本身，谁把它撑高的都算
  observer = new ResizeObserver(() => {
    if (follow) void scrollToBottom()
  })
  observer.observe(inner)

  void scrollToBottom()
})

onUnmounted(() => {
  observer?.disconnect()
  floaterObserver?.disconnect()
  wrapEl?.removeEventListener('scroll', onStreamScroll)
})

// 换会话就换草稿：每个会话各写各的，互不覆盖
watch(
  () => session.currentId,
  (id) => {
    draft.value = loadDraft(id)
    // 换了一份消息，从头（也就是从底部）看起
    follow = true
    void scrollToBottom()
  },
)

// 输入即存本地。localStorage 是同步的、不涉及网络，敲字不会有任何延迟
watch(draft, (text) => saveDraft(session.currentId, text))

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

/**
 * 这一轮现在卡在哪一步的提示文案；没在跑就是空串。
 *
 * 存在的意义是回答「是不是卡了」：请求发出到模型吐第一个字、工具执行中，事件流
 * 都会静默十几秒甚至更久。没有这行字，用户只能盯着不动的界面猜。
 */
const runPhaseText = computed(() => {
  const phase = session.phase
  if (!phase) return ''

  switch (phase.kind) {
    case 'connecting':
      return '正在连接…'
    case 'waiting':
      return '等待模型响应…'
    case 'thinking':
      return '正在思考…'
    case 'generating':
      return '正在生成…'
    case 'tool':
      return `正在执行 ${phase.name}…`
    case 'asking':
      return '等待你的回答…'
  }
})

/** 浮层里有没有东西；它同时决定消息区要不要留白。 */
const hasFloater = computed(
  () =>
    session.liveTodos.length > 0 ||
    recentSubagentEvents.value.length > 0 ||
    Boolean(session.pendingQuestion),
)

/**
 * 浮层高度一变，就同步给消息区的底部留白。
 *
 * 用 ResizeObserver 而不是在几个「可能改变高度」的地方各写一遍：卡片展开、日志追加、
 * 计划正文重排、窗口缩放…… 漏掉任何一处，都会有一段内容被压在浮层下面。
 */
let floaterObserver: ResizeObserver | null = null

watch(floaterEl, (el) => {
  floaterObserver?.disconnect()
  floaterObserver = null

  if (!el) {
    // 浮层收掉了，留白也收掉 —— 不然后面每次滚动都多一块空白
    floaterPad.value = 16
    return
  }

  const sync = (): void => {
    // +20 = 和输入框之间留一点缝，贴死会显得是两个连在一起的框
    floaterPad.value = el.offsetHeight + 20
  }

  floaterObserver = new ResizeObserver(sync)
  floaterObserver.observe(el)
  sync()
})

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
  // 卡片收掉之后消息区会变矮，跟随滚动自己会处理（见上面的 observer），
  // 这里不用再手动滚一次
  await answerQuestion(value)
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

  // 新的一轮从底部看起 —— 用户可能刚才正在翻旧消息，现在注意力回到刚发出的这句
  follow = true
  // 组装的活全在 store 里（见 sendMessage）——这里只管输入框
  await sendMessage({ prompt: text, files })
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
      <!-- 导出当前会话。用普通链接让浏览器自己下载：进度、保存对话框、大文件
           都是它的事，不必为此写一段 fetch -->
      <a
        v-if="session.currentId && session.messages.length"
        class="export"
        :href="`/api/conversations/${session.currentId}/export`"
        download
      >
        导出
      </a>
    </Teleport>

    <el-scrollbar class="stream">
      <!-- ref 挂在这个内容区上：它是「有没有新内容」的观察目标（见 script 里的跟随滚动） -->
      <div
        ref="streamInner"
        class="stream-inner"
        :style="{ paddingBottom: `${floaterPad}px` }"
      >
        <MessageItem v-for="(message, index) in session.messages" :key="index" :message="message" />

        <!-- 执行前确认、子代理看板、任务清单都不在这里了：它们挪到了输入区上方的
             浮层里，理由见 .floater 那段样式说明 -->
        <el-empty v-if="!session.messages.length" description="在下面输入内容，开始对话" />
      </div>
    </el-scrollbar>

    <div class="composer">
      <!-- 子代理看板 + 待确认卡片，作为浮层贴在输出区底部。

           为什么要浮着：它们都是「现在这一轮要你关注 / 要你回答」的东西 ——
           之前放在消息流末尾，用户往上翻两屏就再也看不到，回看历史时更是一眼
           看不到还有个问题在等回答。浮起来之后滚动输出区不影响它们。

           为什么挂在 .composer 里：这样它是相对输入区定位的，输入框长高（多行、
           带附件）时自动跟着上移，不用去同步两个高度。 -->
      <div v-if="hasFloater" ref="floaterEl" class="floater">
        <!-- 这一轮的任务清单。模型每推进一步就整体重写一次，所以这里直接覆盖。

             排在浮层最上面：它是「现在到哪一步了」，属于概览；下面两样是细节和
             要动手的事，越往下越靠近输入框。

             折叠的 —— 收起是一行「完成数 / 总数」，摊开才是全部条目：十几项的
             清单摊开会把输出区吃掉一大块，而多数时候用户只想知道还剩几步。
             跑完之后它才随消息落盘、由 MessageItem 渲染（同一个组件，但那份是
             定稿、不折叠）—— 所以位置会从「浮层」挪进「消息里」，那是定稿，
             不是显示了两遍。 -->
        <TodoList v-if="session.liveTodos.length" :items="session.liveTodos" live collapsible />

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
             排在子代理看板下面：要用户动手的东西放在最靠输入框的位置。 -->
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
      </div>

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

      <!-- 这一轮现在卡在哪一步（见 runPhaseText）。
           放在输入框正上方：它就是「我现在为什么不能打字」的答案，
           也是「慢」和「卡住」之间的区别 -->
      <div v-if="runPhaseText" class="run-phase">
        <el-icon class="spin"><Loading /></el-icon>
        <span>{{ runPhaseText }}</span>
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
/* 导出入口。靠 margin-left:auto 推到标题栏右侧 */
.export {
  margin-left: auto;
  color: var(--text-soft);
  font-size: 13px;
  text-decoration: none;
}

.export:hover {
  color: var(--accent);
}

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

/* 这一轮现在卡在哪一步（见 runPhaseText）。淡色小字，不抢输入框的注意力 */
.run-phase {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 2px 6px;
  font-size: 12px;
  color: var(--text-soft);
}

/* ---------- 浮层：子代理看板 + 待确认卡片 ----------
 * 贴在输入区正上方、盖在输出区之上，滚动输出区不影响它。
 *
 * 为什么不做成消息流里的一块：这两样都是「这一轮要你关注 / 要你回答」的东西，
 * 往上翻两屏就再也看不到 —— 回看历史时更是一眼看不到还有个问题在等回答。
 *
 * 定位用 `bottom: 100%` 而不是手算偏移：输入框长高（多行、带附件）时它会自动
 * 跟着上移，不用去同步两个高度。高度给了上限并在内部滚动 —— 一份长计划不该
 * 把整屏输出区吃掉。
 */
.floater {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 100%;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 46vh;
  overflow-y: auto;
  /* 容器本身不加背景：卡片之间那道缝要能看见底下的对话，才像是「浮在上面」 */
  padding-bottom: 10px;
}

/* 浮起来的卡片加投影，和「平铺在消息流里」的样子区分开。
 * 清单（.todos）是个独立组件、没走 .confirm 那套类名，所以单独列一条 */
.floater .confirm,
.floater .todos {
  box-shadow: 0 8px 24px rgb(0 0 0 / 18%);
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
  /* 浮层的定位基准：它用 bottom: 100% 贴在这个盒子的上沿 */
  position: relative;
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
