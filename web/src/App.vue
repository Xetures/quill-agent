<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { onMounted, ref } from 'vue'

import Sidebar from './components/Sidebar.vue'
import { bootstrap } from './stores/session'
import { errorText } from './utils/error'

/** 启动阶段的错误（后端没起来、接口报错）单独显示，不往各页面里塞。 */
const error = ref('')

onMounted(async () => {
  try {
    await bootstrap()
  } catch (exc) {
    error.value = errorText(exc)
  }
})
</script>

<template>
  <!-- 中文语言包：不配的话表格空态、确认框这些会显示英文 -->
  <el-config-provider :locale="zhCn">
    <div class="shell">
      <Sidebar />

      <main class="main">
        <el-alert
          v-if="error"
          type="error"
          :closable="false"
          title="启动失败"
          :description="`${error}（后端在跑吗？）`"
          class="boot-error"
        />

        <!-- 顶栏：放当前页面的标题与提示。
             内容不在这里写死，而是由各页面用 <Teleport to="#page-head-slot">
             投递过来 —— 标题和提示天然属于页面（只有页面知道自己要说什么），
             但它们得渲染在滚动区之外，Teleport 正好是干这个的。
             别用 v-if 包住这一块：目标元素必须在页面挂载前就存在 -->
        <header class="topbar">
          <div id="page-head-slot" class="topbar-slot"></div>
        </header>

        <RouterView />
      </main>
    </div>
  </el-config-provider>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 240px 1fr;
  height: 100%;
  overflow: hidden;
}

/* 窗口变窄时收一收侧边栏，别让它占掉主区一半 */
@media (max-width: 1000px) {
  .shell {
    grid-template-columns: 200px 1fr;
  }
}

.main {
  display: flex;
  flex-direction: column;
  min-width: 0;

  /* 这两行是「输入框跑出视口」的修复关键。
   * .main 是 grid item，而 grid item 的 min-height 默认是 auto —— 会被内容顶破
   * grid 行，把整个主区撑得比视口还高。外层 .shell 又是 overflow: hidden，
   * 超出的部分被直接裁掉，输入框就这么消失了（连滚动条都没有）。
   * 置 0 才允许它收缩回行高以内。 */
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.boot-error {
  margin: 12px 16px 0;
}

.topbar {
  display: flex;
  align-items: center;
  /* 和侧边栏品牌区同高，两边的下边框因此连成一条直线 */
  height: var(--topbar-height);
  flex-shrink: 0;
  padding: 0 24px; /* 和 .page 的左右内边距对齐，标题不会比页面内容缩进一格 */
  border-bottom: 1px solid var(--border);
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
.topbar h1 {
  flex-shrink: 0;
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.topbar .hint {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  color: var(--text-soft);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
