<script setup lang="ts">
import {
  Box,
  ChatDotRound,
  Collection,
  Cpu,
  MagicStick,
  Plus,
  Setting,
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
  { to: '/models', label: '模型', icon: Cpu },
  { to: '/tools', label: '工具', icon: Tools },
  { to: '/skills', label: '技能', icon: MagicStick },
  { to: '/memory', label: '记忆', icon: Collection },
  { to: '/archived', label: '归档', icon: Box },
  { to: '/settings', label: '设置', icon: Setting },
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
        <p v-if="!session.conversations.length" class="hint">还没有会话，点上面的「新建任务」开始</p>

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
  min-height: 0;
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

/* 归档按钮平时藏起来，hover 当前项时才出现 —— 侧边栏一行放不下两个控件 */
.conv-action {
  flex-shrink: 0;
  opacity: 0;
}

.conv:hover .conv-action {
  opacity: 1;
}

.conv.active .conv-action {
  color: var(--on-accent);
}
</style>
