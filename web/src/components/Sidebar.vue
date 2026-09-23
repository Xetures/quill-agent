<script setup lang="ts">
import {
  Box,
  Collection,
  Cpu,
  DataLine,
  Document,
  Download,
  Loading,
  MagicStick,
  Plus,
  Setting,
  Stamp,
  Tools,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 毛玻璃蓝版图标（小尺寸简化版：这一格只有 40px，设计稿给的 02c 就是为小尺寸准备的
// —— 大尺寸那一版细节多，缩到 40px 会糊成一团灰）。换图标的理由见 Quill Glass Blue
// 的说明：同一页面里不要混用深蓝与柔蓝两版图标
import quillIcon from '../assets/quill-icon-glass.svg'
import { archiveConversation, loadMessages, session, startNewConversation } from '../stores/session'
import { formatTime } from '../utils/format'
import HelpButton from './HelpButton.vue'

// ElMessage / ElMessageBox 由 unplugin-auto-import 自动引入（样式也一并带上），
// 所以这个文件里看不到它们的 import —— 这是按需引入的代价：省了 import，
// 但读代码时要知道它们从哪来

const route = useRoute()
const router = useRouter()

interface NavItem {
  to: string
  label: string
  icon: Component
}

/**
 * 导航项。
 *
 * 没有「任务」—— 它的入口是上面的「新建任务」按钮和下面的会话列表：
 * 想继续聊就点会话，想开新的就点按钮，都不需要「切到任务页」这个动作。
 *
 * 「偏好设置」是一级项，但页面里还有一层二级导航（主题 / 对话 / 联网搜索 / 关于，
 * 见 SettingsLayout）。二级导航不放在侧边栏：那要先点开父级才看得到几节，
 * 而这几节本来就互相独立，每次换一节都要两步。
 */
const NAV: NavItem[] = [
  { to: '/modes', label: '模式', icon: MagicStick },
  { to: '/prompts', label: '提示词', icon: Document },
  { to: '/tools', label: '工具', icon: Tools },
  { to: '/skills', label: '技能', icon: Stamp },
  { to: '/memory', label: '记忆', icon: Collection },
  { to: '/usage', label: '用量', icon: DataLine },
  { to: '/models', label: 'API设置', icon: Cpu },
  // 归档收进了偏好设置（见 SettingsLayout / router.ts）—— 它是「回头翻旧账」，
  // 用得比上面这些少，不必常驻一级导航
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
    <!-- 品牌区：LOGO + 应用名 + 使用说明入口。它和主区顶边栏是并排的**两块**玻璃板，
         高度取同一个变量（见 --topbar-height）。
         说明入口放这里而不是主区顶边栏：它讲的是「这个应用怎么用」，属于品牌这一类的
         常驻信息 —— 挨着 LOGO 比挤在任务页标题旁边更说得通，那边也就能空出来给
         和当前任务真正相关的操作（比如执行权限） -->
    <div class="brand">
      <img class="logo" :src="quillIcon" alt="Quill" />
      <div class="brand-text">
        <div class="app-name">Quill</div>
        <div class="tagline">本地 Agent 工作台</div>
      </div>
      <HelpButton />
    </div>

    <!-- 侧栏板：品牌板以下的内容各自装在这块玻璃里。
         品牌区与它分成两块板（而不是一块通高的板）：这样左下角的 LOGO 板才能和顶边栏
         对齐成同一条横带，四个区域也因此是四块独立浮起的板（见 Quill Glass Blue） -->
    <div class="panel">
      <!-- 品牌区以下是一整列：新建任务、导航、会话列表。
           滚动**只发生在会话列表里**（见 `.list`）—— 导航是常驻的入口，滚起来之后
           它跑到屏幕外，想换个页面还得先滚回去 -->
      <div class="scroll-body">
        <!-- 「任务」的入口。做成整块的金色按钮：它是这个应用最常用的动作，
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
              <!-- 正在跑的会话转个圈：切走之后它还在后台跑着（运行跟着会话走，不跟着
                   界面走）。没有这个标记，用户没法知道那个任务是还在跑还是早停了 -->
              <el-icon
                v-if="session.runningIds.includes(item.id)"
                class="conv-running spin"
                title="正在运行"
              >
                <Loading />
              </el-icon>

              <span class="conv-title">{{ item.title }}</span>
              <!-- 导出这个会话：贴在归档左边。用图标不带文字 —— 一行里并排两个动作，
                   再写「导出」两个字会把标题挤没。
                   它是 `<a download>`（tag="a"）：进度、保存对话框、大文件都是浏览器
                   自己的事，不必为此写一段 fetch。@click.stop 免得顺带切了会话 -->
              <el-button
                tag="a"
                size="small"
                text
                class="conv-action"
                :icon="Download"
                :href="`/api/conversations/${item.id}/export`"
                download
                title="导出"
                @click.stop
              />
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
    </div>
  </aside>
</template>

<style scoped>
/* 左列是**两块**玻璃板：上面 LOGO 板、下面侧栏板。
 * 两块都浮在背景层上，所以这里不再画背景和右边框 —— 边界交给板子自己的圆角与描边 */
/* 品牌板与侧栏板之间。间距与 .shell / .main 取同一个值 ——
 * 它得压得住圆角（见 App.vue 里「间距与圆角配对」那段说明），
 * 三处不一致的话接缝看着就不是一套的 */
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 20px;
  height: 100%;
  overflow: hidden;
}

/* 玻璃配方（与顶边栏、任务区同一份，见 style.css 里的 --glass-* 变量）。
 * 底色与投影由全局那条规则统一给、模糊在 .shell 那一层做一次（见 style.css 的
 * 「玻璃的底色与模糊，分在两层上」）；这里只留描边与圆角 */
.brand,
.panel {
  overflow: hidden;
  border: 1px solid var(--glass-border);
  border-radius: var(--glass-radius);
}

/* 侧栏板：吃掉品牌板之外的全部高度，内部由 .scroll-body 自己滚 */
.panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* 品牌区以下的一整列：新建任务、导航、会话列表。
 *
 * 它自己**不滚**：滚动交给里面的会话列表（`.list`）。让这一整列滚的话，导航会随着
 * 滚动跑出可视区 —— 它是常驻入口，用户往下翻了几屏再想换页面就得先滚回去。
 * 窗口再矮也只压会话列表（`flex: 1` + `min-height: 0`），导航和新建按钮原地不动。
 *
 * overflow-x 显式写 hidden：横向不写的话会被内容顶出一条横向滚动条。 */
.scroll-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: hidden;
}

/* ---------- 品牌区 ---------- */

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  /* 高度和主区顶边栏取同一个变量：两者在同一水平带上，必须严格等高 */
  height: var(--topbar-height);
  flex-shrink: 0;
  padding: 0 16px;
}

.logo {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  /* 玻璃版图标本身就是一块圆角玻璃板，这里只补一点投影让它从玻璃板上浮起来；
   * 圆角交给图标自己（SVG 里的 rx 和这个值是同一个比例） */
  border-radius: 11px;
  box-shadow: var(--shadow-sm);
}

/* flex: 1 让它占满剩余宽度，右侧的说明入口因此自然贴到边上（不必给那边加 margin）。
 * 代价是这块会被压缩，所以要能收敛：min-width: 0 加上下面两行省略号 ——
 * 否则窄窗口下副标题会顶到按钮上去 */
.brand-text {
  flex: 1;
  min-width: 0;
}

.app-name,
.tagline {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* 主按钮走**冰雾高光**（取值见 style.css 的 --ice-*，两套主题各一份）。
 * 它比原来的琥珀金更克制 —— 金色只留小面积（笔尖、行内代码）。
 *
 * 需要连 --el-button-* 一串变量一起改：直接写 background 的话，Element 的
 * hover / active 又会把底色刷回主色 */
.new-task-btn {
  width: 100%;
  border: none;
  font-weight: 600;
  background: var(--ice-bg);
  box-shadow: var(--ice-shadow);
  color: var(--ice-text);

  --el-button-bg-color: transparent;
  --el-button-text-color: var(--ice-text);
  --el-button-border-color: transparent;
  --el-button-hover-bg-color: transparent;
  --el-button-hover-text-color: var(--ice-text);
  --el-button-hover-border-color: transparent;
  --el-button-active-bg-color: transparent;
  --el-button-active-text-color: var(--ice-text);
  --el-button-active-border-color: transparent;
}

.new-task-btn:hover {
  filter: brightness(1.03);
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
  /* 写 0 而不是给下限：这一列已经不滚了（见 .scroll-body），给下限的话窗口一矮
   * 总高就超出侧栏板，底部会被 overflow: hidden 裁掉 —— 会话看得见半截却滚不动。
   * 现在压的是这一块，压小到几行也仍然能滚 */
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

/* 选中的会话：**冰雾高光**（和主按钮同一套）。
 * 从前铺的是主色实底，深色模式下那是暖金 —— 一整行金色在侧栏里很抢眼，
 * 也把「选中」说得比「正在运行」还重 */
.conv.active {
  background: var(--ice-bg);
  color: var(--ice-text);
}

/* 正在跑的标记。跟着行文本色走（currentColor）：选中行是主色底，
 * 写死颜色的话在那里就会糊掉看不见 */
.conv-running {
  flex-shrink: 0;
  font-size: 12px;
}

.spin {
  animation: conv-spin 0.8s linear infinite;
}

@keyframes conv-spin {
  to {
    transform: rotate(360deg);
  }
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
  /* Element 默认给相邻按钮留 12px 外边距（`.el-button + .el-button`），两个 24px 的
   * 图标按钮之间因此空出一道比按钮还宽的沟。这一行本来就窄，挤掉它 ——
   * 间距交给 .conv 的 gap */
  margin-left: 0;

  --el-button-text-color: var(--text-soft);
  --el-button-hover-text-color: var(--accent);
  --el-button-hover-bg-color: var(--bg-hover);
  --el-button-active-text-color: var(--accent);
}

.conv:hover .conv-action {
  opacity: 1;
}

/* 选中行整块是冰雾底，图标要换成压在上面的深墨色才看得见 */
.conv.active .conv-action {
  opacity: 0.9;

  --el-button-text-color: var(--ice-text);
  --el-button-hover-text-color: var(--ice-text);
  --el-button-active-text-color: var(--ice-text);
}

/* 选中行上，悬停底色必须单独指定。
 *
 * Element 的文本按钮 hover 用的是 `--el-fill-color-light`（我们把它映射成了
 * --bg-soft，半透明白），**不是** `--el-button-hover-bg-color` —— 所以改那个变量没用。
 * 而半透明白压在冰雾底上是一块发白的斑，不像「按下去了一点」。
 *
 * 换成深墨的淡色：看得出亮了，图标仍是深墨色，不会盖掉自己。
 *
 * 选择器里的 `:not(.is-disabled)` 是为了压过 EP 的
 * `.el-button.is-text:not(.is-disabled):hover` —— 两者同权重时谁在后面谁说了算，
 * 而注入顺序不可靠，所以干脆多加一档权重。 */
.conv.active .conv-action:not(.is-disabled):hover {
  background-color: rgba(27, 58, 92, 0.14);
}

.conv.active .conv-action:not(.is-disabled):active {
  background-color: rgba(27, 58, 92, 0.14);
}
</style>
