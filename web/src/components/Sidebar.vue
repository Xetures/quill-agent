<script setup lang="ts">
import {
  Box,
  ChatDotRound,
  Collection,
  Cpu,
  DataLine,
  Document,
  Link,
  MagicStick,
  Plus,
  Setting,
  Stamp,
  Tools,
} from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import quillIcon from '../assets/quill-icon.svg'
import { archiveConversation, loadMessages, session, startNewConversation } from '../stores/session'
import { formatTime } from '../utils/format'

// ElMessage / ElMessageBox 由 unplugin-auto-import 自动引入（样式也一并带上），
// 所以这个文件里看不到它们的 import —— 这是按需引入的代价：省了 import，
// 但读代码时要知道它们从哪来

const route = useRoute()
const router = useRouter()

/**
 * 导航项。
 *
 * 没有「任务」—— 它的入口是上面的「新建任务」按钮和下面的会话列表：
 * 想继续聊就点会话，想开新的就点按钮，都不需要「切到任务页」这个动作。
 */
const NAV = [
  { to: '/modes', label: '模式', icon: MagicStick },
  { to: '/prompts', label: '提示词', icon: Document },
  { to: '/tools', label: '工具', icon: Tools },
  { to: '/skills', label: '技能', icon: Stamp },
  { to: '/memory', label: '记忆', icon: Collection },
  { to: '/archived', label: '归档', icon: Box },
  { to: '/usage', label: '用量', icon: DataLine },
  { to: '/models', label: 'API设置', icon: Cpu },
  { to: '/search', label: '联网搜索', icon: Link },
  { to: '/settings', label: '偏好设置', icon: Setting },
]

async function onNew(): Promise<void> {
  // 当前就是个空会话的话直接复用，不再建一个 —— 否则连着点几下会堆出一串空会话，
  // 而且它们标题都一样，清理起来很烦
  if (session.messages.length) await startNewConversation()

  // 新建会话就是为了聊天，直接跳回任务页
  if (route.path !== '/') await router.push('/')
}

async function onOpen(id: string): Promise<void> {
  if (route.path !== '/') await router.push('/')
  await loadMessages(id)
}

async function onArchive(id: string, title: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`归档「${title}」？空会话会被直接删除。`, '归档会话', {
      confirmButtonText: '归档',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户按了取消
  }

  const archived = await archiveConversation(id)
  ElMessage.success(archived ? '已归档' : '空会话已删除')
}
</script>

<template>
  <aside class="sidebar">
    <!-- 品牌区：LOGO + 应用名。高度和主区顶栏一致，见 --topbar-height -->
    <div class="brand">
      <img class="logo" :src="quillIcon" alt="quill" />
      <div class="brand-text">
        <div class="app-name">quill</div>
        <div class="tagline">本地 Agent 工作台</div>
      </div>
    </div>

    <!-- 品牌区以下的整块内容放进滚动容器：导航项和「会话」标题高度固定、
         不允许压缩，窗口一矮（或浏览器放大）它们的总高就超过可视区，
         没有这层滚动的话底部会被 overflow: hidden 直接裁掉，什么也看不到 -->
    <div class="scroll-body">
      <!-- 「任务」的入口。做成整块的主色按钮：它是这个应用最常用的动作，
           不该和下面那排平级的导航项长得一样 -->
      <div class="new-task">
        <el-button type="primary" :icon="Plus" class="new-task-btn" @click="onNew">
          新建任务
        </el-button>
      </div>

      <el-menu :default-active="route.path" router class="nav">
        <el-menu-item v-for="item in NAV" :key="item.to" :index="item.to">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <div class="section">
        <!-- 新建会话的入口已经在上面的主按钮里了，这里只留分组标题 -->
        <div class="section-head">
          <span>会话</span>
        </div>

        <el-scrollbar class="list">
          <p v-if="!session.conversations.length" class="hint">
            还没有会话，点上面的「新建任务」开始
          </p>

          <div
            v-for="item in session.conversations"
            :key="item.id"
            class="conv"
            :class="{ active: item.id === session.currentId }"
            :title="`最后更新：${formatTime(item.updated_at)}`"
            @click="onOpen(item.id)"
          >
            <span class="conv-title">{{ item.title }}</span>
            <el-button
              size="small"
              text
              class="conv-action"
              :icon="Box"
              title="归档"
              @click.stop="onArchive(item.id, item.title)"
            />
          </div>
        </el-scrollbar>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--bg-soft);
  border-right: 1px solid var(--border);
}

/* 品牌区以下的整块内容。导航和「会话」标题高度固定、不允许压缩，
 * 窗口一矮它们的总高就超出可视区；没有这层滚动就会被 .sidebar 的
 * overflow: hidden 裁掉（底部直接看不见）。
 * overflow-x 显式写 hidden：纵向给了 auto 之后，横向会跟着变成 auto，
 * 容易平白多出一条横向滚动条 */
.scroll-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

/* ---------- 品牌区 ---------- */

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  /* 高度和主区顶栏取同一个变量：两者在同一水平带上，必须严格等高 */
  height: var(--topbar-height);
  flex-shrink: 0;
  padding: 0 14px;
  border-bottom: 1px solid var(--border);
}

.logo {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  /* 图标本身就是一块深蓝底的圆角方块，这里只补一点投影让它在浅色侧边栏上
   * 有厚度；圆角交给图标自己（SVG 里的 rx 和这个值是同一个比例） */
  border-radius: 9px;
  box-shadow: var(--shadow-sm);
}

.brand-text {
  min-width: 0;
}

.app-name {
  font-size: 16px;
  font-weight: 650;
  letter-spacing: 0.3px;
  line-height: 1.25;
}

.tagline {
  font-size: 11px;
  line-height: 1.3;
  color: var(--text-soft);
}

/* ---------- 新建任务 ---------- */

.new-task {
  flex-shrink: 0;
  padding: 12px 10px 4px;
}

.new-task-btn {
  width: 100%;
}

/* ---------- 导航 ---------- */

.nav {
  flex-shrink: 0;
  padding: 8px 0;
  border-right: none;
  background: transparent;

  /* 覆盖 Element Plus 的菜单默认值：
   * 背景透明（跟着侧边栏走）、压缩行高（几项才不至于把会话区挤没） */
  --el-menu-bg-color: transparent;
  --el-menu-hover-bg-color: var(--bg-hover);
  --el-menu-item-height: 38px;
  --el-menu-item-font-size: 13px;
}

/* 菜单项默认是「通栏高亮」，加圆角和内缩更像现代侧边栏 */
.nav :deep(.el-menu-item) {
  margin: 0 8px 2px;
  border-radius: 7px;
}

.nav :deep(.el-menu-item.is-active) {
  background: var(--accent-soft);
  font-weight: 500;
}

/* ---------- 会话区 ---------- */

.section {
  display: flex;
  flex-direction: column;
  flex: 1;
  /* 刻意不写 0：会话区被压成 0 高时，它的内容会直接溢到导航项上叠成一团。
   * 给个下限（标题 + 三四行会话），窗口不够高就交给外层 .scroll-body 出滚动条 */
  min-height: 132px;
  padding: 0 10px 10px;
  border-top: 1px solid var(--border);
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px 4px;
  font-size: 12px;
  color: var(--text-soft);
}

.list {
  flex: 1;
  min-height: 0;
}

.hint {
  margin: 4px;
  font-size: 12px;
  color: var(--text-soft);
}

.conv {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 5px 2px 5px 9px;
  border-radius: 6px;
  cursor: pointer;
}

.conv:hover {
  background: var(--bg-hover);
}

.conv.active {
  background: var(--accent);
  color: var(--on-accent);
}

.conv-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

/* 归档按钮：平时就露出来一点（让人知道这一行有操作），hover 时再提亮。
 *
 * 颜色必须走 Element 的 CSS 变量，不能直接写 `color` —— 直接写的优先级输给
 * `.el-button.is-text`，选中行（整块主色底）上的图标会保持深色，等于看不见。
 * 改变量就是在同一个元素上换个取值，稳。 */
.conv-action {
  flex-shrink: 0;
  opacity: 0.8;
  transition: opacity 0.15s ease;

  --el-button-text-color: var(--text-soft);
  --el-button-hover-text-color: var(--accent);
  --el-button-hover-bg-color: var(--bg-hover);
  --el-button-active-text-color: var(--accent);
}

.conv:hover .conv-action {
  opacity: 1;
}

/* 选中行整块是主色底，图标要换成压在主色上的那个颜色才看得见 */
.conv.active .conv-action {
  opacity: 0.9;

  --el-button-text-color: var(--on-accent);
  --el-button-hover-text-color: var(--on-accent);
  --el-button-active-text-color: var(--on-accent);
}

/* 选中行上，悬停底色必须单独指定。
 *
 * Element 的文本按钮 hover 用的是 `--el-fill-color-light`（我们把它映射成了
 * --bg-soft，米白），**不是** `--el-button-hover-bg-color` —— 所以改那个变量没用。
 * 而米白底 + 米白图标（--on-accent）几乎同色，一悬停图标就糊成一个色块。
 *
 * 换成比选中底色稍亮的 --accent-hover：看得出「亮了」，图标仍然是米白，不会盖掉自己。
 *
 * 选择器里的 `:not(.is-disabled)` 是为了压过 EP 的
 * `.el-button.is-text:not(.is-disabled):hover` —— 两者同权重时谁在后面谁说了算，
 * 而注入顺序不可靠，所以干脆多加一档权重。 */
.conv.active .conv-action:not(.is-disabled):hover {
  background-color: var(--accent-hover);
}

.conv.active .conv-action:not(.is-disabled):active {
  background-color: var(--accent-hover);
}
</style>
