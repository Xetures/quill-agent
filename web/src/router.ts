import { createRouter, createWebHistory } from 'vue-router'

/**
 * 路由表。
 *
 * 用浏览器路由：切页面不再重跑整个应用，每个页面还能直接分享链接。
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: () => import('./views/ChatView.vue') },
    // 模式排在最前：它是「这个 Agent 是什么样子」的总配置，其余页面都是给它备料
    { path: '/modes', name: 'modes', component: () => import('./views/ModesView.vue') },
    { path: '/prompts', name: 'prompts', component: () => import('./views/PromptsView.vue') },
    // 模型连接页在导航里叫「API 设置」：那一页管的就是各家 API 的地址与 Key
    { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue') },
    { path: '/tools', name: 'tools', component: () => import('./views/ToolsView.vue') },
    { path: '/skills', name: 'skills', component: () => import('./views/SkillsView.vue') },
    { path: '/memory', name: 'memory', component: () => import('./views/MemoryView.vue') },
    { path: '/archived', name: 'archived', component: () => import('./views/ArchivedView.vue') },
    // 用量是「回来的看」的页面，排在归档之后、设置之前
    { path: '/usage', name: 'usage', component: () => import('./views/UsageView.vue') },
    // 偏好设置：侧边栏是一级项，页面里还有一层二级导航（见 SettingsLayout）。
    // 各节做成子路由而不是堆在一页里，是因为它们的体量差得太远 ——「联网搜索」自己就是
    // 一整页的配置（后端、Key、端点、试搜），和「主题」并排只会互相埋掉。
    {
      path: '/settings',
      component: () => import('./views/SettingsLayout.vue'),
      redirect: '/settings/theme',
      children: [
        {
          path: 'theme',
          name: 'settings-theme',
          component: () => import('./views/SettingsThemeView.vue'),
        },
        {
          path: 'chat',
          name: 'settings-chat',
          component: () => import('./views/SettingsChatView.vue'),
        },
        {
          path: 'search',
          name: 'settings-search',
          component: () => import('./views/SearchView.vue'),
        },
        {
          path: 'mcp',
          name: 'settings-mcp',
          component: () => import('./views/McpView.vue'),
        },
        {
          path: 'about',
          name: 'settings-about',
          component: () => import('./views/SettingsAboutView.vue'),
        },
      ],
    },
    // 联网搜索原先是侧边栏一级项，现在归到「偏好设置」下面。旧地址留跳转，
    // 免得书签和浏览器历史里的 /search 落到一片空白上
    { path: '/search', redirect: '/settings/search' },
  ],
})
