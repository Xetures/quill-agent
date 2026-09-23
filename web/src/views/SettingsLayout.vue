<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
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
const { t } = useI18n()

/**
 * 二级导航项。顺序就是阅读顺序：先看外观，再看对话，接着外部服务，最后是归档与版本。
 *
 * 第一节的地址仍叫 `theme`：那一页最早只管配色，后来把字号与语言也收了进来
 * （见 SettingsThemeView）—— 改名要动路由与既有链接，收益只是名字好看，所以留着。
 *
 * computed 而不是模块级常量：文案来自 `t()`，语言一换得跟着重算。
 */
const SECTIONS = computed(() => [
  { to: '/settings/theme', label: t('settings.sections.display') },
  { to: '/settings/chat', label: t('settings.sections.chat') },
  { to: '/settings/search', label: t('settings.sections.search') },
  { to: '/settings/mcp', label: t('settings.sections.mcp') },
  // 归档从侧栏一级项挪到这里：它是「回头翻旧账」，和显示、对话这些设置归在一处
  { to: '/settings/archived', label: t('settings.sections.archived') },
  { to: '/settings/about', label: t('settings.sections.about') },
])
</script>

<template>
  <div class="page settings-shell">
    <Teleport to="#page-head-slot">
      <h1>{{ t('settings.title') }}</h1>
      <span class="hint">{{ t('settings.hint') }}</span>
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
 * sticky 试过：.page 顶部 20px 内边距在吸顶位置之上，会漏出一条细缝。
 *
 * 类名**刻意不叫 `.shell`**：App.vue 里那个窗口级容器就叫这名，而 Vue 的 scoped CSS
 * 会把父组件的 scope 标记加到子组件**根元素**上 —— 于是 App.vue 的 `.shell` 规则会直接
 * 命中这里，把窗口布局整套灌进来：
 *   - 它的 `background: var(--seam-tint)`（浅色下是 transparent）比 `.page` 的白玻璃
 *     优先级高，设置板的玻璃底会被顶掉，露出模糊的蓝壁纸（底色看着偏蓝）；
 *   - 它的 `padding-top`（桌面壳下是 20+28+12=60px）盖掉 `.page` 的 20px，
 *     二级导航被推下去一大截，白占一块地方。
 * 换个独有的名字，这类冲突就不可能再发生。 */
.settings-shell {
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
  font-size: var(--fs-sm);
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
