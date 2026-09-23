<script setup lang="ts">
import en from 'element-plus/es/locale/lang/en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import ClipboardPanel from './components/ClipboardPanel.vue'
import MemoPanel from './components/MemoPanel.vue'
import SandboxButton from './components/SandboxButton.vue'
import Sidebar from './components/Sidebar.vue'
import { bootstrap } from './stores/session'
import { locale } from './locales'
import { errorText } from './utils/error'

const { t } = useI18n()

/** 启动阶段的错误（后端没起来、接口报错）单独显示，不往各页面里塞。 */
const error = ref('')

/**
 * Element Plus 自己的文案（表格空态、确认框按钮、分页）跟着界面语言走。
 * 它和我们自己的文案是两套来源：前者由 EP 语言包提供，后者在 `locales/` 里。
 */
const epLocale = computed(() => (locale.value === 'en-US' ? en : zhCn))

onMounted(async () => {
  try {
    await bootstrap()
  } catch (exc) {
    error.value = errorText(exc)
  }
})
</script>

<template>
  <!-- EP 的语言包：不配的话表格空态、确认框这些会显示英文 -->
  <el-config-provider :locale="epLocale">
    <div class="shell">
      <Sidebar />

      <main class="main">
        <el-alert
          v-if="error"
          type="error"
          :closable="false"
          :title="t('app.bootFailedTitle')"
          :description="t('app.bootFailedDesc', { error })"
          class="boot-error"
        />

        <!-- 顶栏：放当前页面的标题与提示。
             内容不在这里写死，而是由各页面用 <Teleport to="#page-head-slot">
             投递过来 —— 标题和提示天然属于页面（只有页面知道自己要说什么），
             但它们得渲染在滚动区之外，Teleport 正好是干这个的。
             别用 v-if 包住这一块：目标元素必须在页面挂载前就存在 -->
        <header class="topbar">
          <div id="page-head-slot" class="topbar-slot"></div>

          <!-- 执行权限常驻在顶栏右侧：它和当前在哪个页面无关，放这里任何页面都够得着，
               也不必每个页面各写一个入口。
               特意**不放**输入框那一排 —— 那排是「这一轮」的选择，而沙箱是整台机器的
               边界，放一起会让人以为它只对当前这次对话生效。
               （使用说明挪进了侧边栏品牌区，见 Sidebar.vue） -->
          <SandboxButton />
        </header>

        <RouterView />
      </main>

    </div>

    <!-- 公共剪贴板：挂在最外层，切页面不丢（它服务的就是「跨页面取用」）。
         **必须放在 `.shell` 外面**：它用的是 `position: fixed`，而 `.shell` 上有
         `backdrop-filter` —— 那会让 `.shell` 成为 fixed 后代的**定位基准**（包含块），
         fixed 就不再相对窗口了。偏偏桌面壳里 `.shell` 还要整体上移 28pt 把标题栏那一条
         让出来，于是那条常驻的右侧触发条会跟着上移、上边被切到窗口外面去（用户报的
         「剪贴板上边超出边界」）。
         挪出来之后，它的 fixed 就是这个窗口，与 `.shell` 怎么摆无关。 -->
    <ClipboardPanel />

    <!-- 公共备忘录：和剪贴板是一对，方向相反 —— 它从**左边**拉出，内容是用户自己敲的
         （剪贴板是被动收集的复制记录）。同样必须放在 `.shell` 外面，理由见上一条。 -->
    <MemoPanel />
  </el-config-provider>
</template>

<style scoped>
/* 四块玻璃板的网格：左列是 LOGO 板与侧栏板（Sidebar 内部再分成两块），
 * 右列是顶边栏与任务区。padding 就是背景层从板子四周露出来的那圈 —— 没有它，
 * 板子会顶到窗口边缘，玻璃也就没有「浮在背景上」的观感了（见 Quill Glass Blue）。
 *
 * **间距与圆角是一组，得配着看**：缝里露出的是没经过玻璃的原始背景，
 * 板上是「提亮 + 模糊」过的，两者本来就差一档。这个差值在四块板交汇处最明显 ——
 * 那块背景露出的面积不是「缝」而是「缝 + 两侧圆角各自缩进去的一段」，
 * 圆角一旦比缝还大，交汇处就从「窄缝」鼓成一个方的洞。
 * 所以间距要明显大于圆角（原来是 14px 对 18px，反着来的）。
 *
 * **模糊在这里做，只在这一次**（见 style.css 那段说明）：整个窗口共用一层
 * 毛玻璃，四块板与它们之间的缝都在这张模糊过的背景上 —— 板只叠一层半透明白。
 * 从前模糊是每块板各做一次、缝里是没模糊的原始背景，板的边缘两侧清晰度突变，
 * 看着像「这里接了一块别的东西」。
 *
 * `background` 是垫在缝里的那层底色（见 style.css 的 --seam-tint）：
 * 深色模式下它把缝提一档，四条缝不再是四道深沟 */
.shell {
  display: grid;
  grid-template-columns: 236px 1fr;
  gap: 20px;
  height: 100%;
  padding: 20px;
  /* 桌面壳里顶边**多让出一截**给系统红绿灯 + 一道余量（见下面那段 :global） */
  padding-top: calc(20px + var(--titlebar-band, 0px) + var(--titlebar-gap, 0px));
  background: var(--seam-tint);
  overflow: hidden;
}

/* 桌面壳（pywebview）里，窗口内容延伸进了系统标题栏，红绿灯浮在窗口左上角 ——
 * 界面据此从标题栏高度开始画，把那一条让出来（见 desktop.py 的
 * `_blend_titlebar_into_ui`）。
 *
 * 让出来的那一条画的是 .shell 自己的背景（--seam-tint + 同一层模糊），所以它和
 * 左右下三边的留白是**同一片底**，不是另行补的一条带 —— 红绿灯就浮在这片底上，
 * 既不压 UI，也不会露出一截系统底色。
 *
 * 浏览器里没有这个类，变量取默认值 0，一切照旧。
 * 写成变量而不是两处写死 28px：宽屏与窄屏的基准间距不同（20 / 16），
 * 一个数字改一处即可。 */
:global(html.in-desktop) {
  /* 红绿灯那一条的高度。板子要**上移**这么多才能顶到窗口最上边 */
  --titlebar-band: 28px;
  /* 红绿灯与板内内容之间再留一道余量。红绿灯本身只有 28pt，紧贴着板就能看见
   * 「灯压着内容」—— 这一道是给它们的呼吸空间 */
  --titlebar-gap: 12px;
}

/* 桌面壳里让玻璃板**从窗口顶端开始**，红绿灯浮在板上（Safari / 备忘录那种观感），
 * 而板内的内容照旧让开那一条 —— 靠「整体上移 + 内边距补回来」这对组合：前者把板子的
 * 上边界挪到窗口顶，后者把内容按回红绿灯下面，一进一出正好抵消，内容位置和浏览器里
 * 完全一样（所以上面那两处 padding-top 不用再动）。
 *
 * 只加 padding 是不够的（前一版就是那么写的）：板子被整体推下去，顶部空出一条没内容的
 * 底色带 —— 那正是「红绿灯还浮在一条灰条上」的来源。
 *
 * 高度也要补回来：上移 28 之后如果高度不变，底下会空出 28。 */
:global(html.in-desktop) .shell {
  margin-top: calc(-1 * var(--titlebar-band));
  height: calc(100% + var(--titlebar-band));
}

/* 窗口变窄时收一收侧边栏，别让它占掉主区一半。
 * 间距同样收一档（见上面那段：它得压得住圆角） */
@media (max-width: 1000px) {
  .shell {
    grid-template-columns: 208px 1fr;
    gap: 18px;
    padding: 16px;
    padding-top: calc(16px + var(--titlebar-band, 0px) + var(--titlebar-gap, 0px));
  }
}

/* 主区自己不是一块板：它只是把顶边栏与页面两块板竖着排开。
 * 间距与 .shell 取同一个值（见那里对「间距与圆角配对」的说明） */
.main {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.boot-error {
  margin: 12px 16px 0;
}

/* 顶边栏：第二块玻璃板。它和左下角的 LOGO 板同高（同一个变量），
 * 两者并排一眼能看出是同一水平带上的两块。
 *
 * position: relative 是给里面的运行状态提示用的 —— 它在顶栏正中绝对定位
 * （见 ChatView 的 .run-phase），要有个定位祖先才不会跑到窗口中间去 */
.topbar {
  position: relative;
  display: flex;
  align-items: center;
  height: var(--topbar-height);
  flex-shrink: 0;
  padding: 0 22px; /* 和侧栏板的内边距取齐，标题不会看着比别处缩进一格 */
  overflow: hidden;
}

.topbar-slot {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}
</style>

<!-- 刻意不加 scoped：顶栏里的内容是各页面 Teleport 过来的，那些节点的 data-v
     属性属于各自的页面组件，scoped 规则选择不到它们。
     这样安排也顺带定下了约定 —— 顶栏长什么样由顶栏决定，页面只管按 <h1> 和
     .hint 这两个约定写内容，不必六个页面各写一遍边距和字号 -->
<style>
/* 页面节点要吃掉顶边栏之外的全部高度。`.page` 与 `.chat` 都是**别的组件**的根，
 * scoped 选择器带 data-v 属性、选不到它们 —— 所以这条必须写在非 scoped 块里。
 *
 * 覆盖 `.page` 的 height:100%：在带 gap 的 flex 列里，100% 是「容器全高」，
 * 加上顶边栏和 gap 就会顶出容器 */
.main > .page,
.main > .chat {
  flex: 1;
  height: auto;
  min-height: 0;
}

.topbar h1 {
  flex-shrink: 0;
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: 600;
}

.topbar .hint {
  min-width: 0;
  overflow: hidden;
  font-size: var(--fs-xs);
  color: var(--text-soft);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 顶栏右侧的执行权限按钮永远压在最上层。顶栏中间那一格是绝对定位的
 * （见 ChatView 的 `.head-center`），万一某页标题很长把中间格撑到这边来，
 * 也不能挡住这个入口 —— 它点不开等于权限改不了，是「界面看着在、其实废了」的那类故障 */
.topbar .sandbox {
  position: relative;
  z-index: 1;
}
</style>
