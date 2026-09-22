<script setup lang="ts">
import { useRoute } from 'vue-router'

/**
 * 偏好设置的外壳：顶边栏下方的二级导航 + 内容区。
 *
 * 二级导航为什么摊在页面顶部，而不是做成侧边栏的二级菜单：侧边栏那个版本要先点开
 * 父级才看得到几节，而这几节本来就互相独立 —— 每次换一节都要「展开 → 再点」两步。
 * 摊在页面顶部是一眼可见、一次点击。
 *
 * 各节的正文是子路由（见 router.ts），本页只管导航和外壳。
 */
const route = useRoute()

/** 二级导航项。顺序就是阅读顺序：先看外观，再看对话，最后是外部服务与版本。 */
const SECTIONS = [
  { to: '/settings/theme', label: '主题' },
  { to: '/settings/chat', label: '对话' },
  { to: '/settings/search', label: '联网搜索' },
  { to: '/settings/mcp', label: 'MCP' },
  { to: '/settings/about', label: '关于' },
]
</script>

<template>
  <div class="page shell">
    <Teleport to="#page-head-slot">
      <h1>偏好设置</h1>
      <span class="hint">界面外观、对话预算与外部服务的配置</span>
    </Teleport>

    <!-- 二级导航。`.page` 的内容就从顶边栏下面开始，所以它在视觉上紧贴顶边栏 -->
    <nav class="sub-nav">
      <RouterLink
        v-for="item in SECTIONS"
        :key="item.to"
        :to="item.to"
        class="sub-item"
        :class="{ active: route.path === item.to }"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

    <div class="scroll">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.sub-nav {
  display: flex;
  gap: 2px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

/* 滚动收进内容区，导航不动（它是「我在哪一节」的路标）。
 * sticky 试过：.page 顶部 20px 内边距在吸顶位置之上，会漏出一条细缝 */
.shell {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

/* 下划线式高亮：比整块底色克制，也不会让人误以为这是「切换页面」级别的导航 */
.sub-item {
  padding: 7px 12px;
  /* 压住容器那条分隔线，选中时下划线才像是与它连上了 */
  margin-bottom: -1px;
  border-bottom: 2px solid transparent;
  color: var(--text-soft);
  font-size: 13px;
  text-decoration: none;
  transition:
    color 0.15s ease,
    border-color 0.15s ease;
}

.sub-item:hover {
  color: var(--text);
}

.sub-item.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
</style>
