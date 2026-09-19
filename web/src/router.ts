import { createRouter, createWebHistory } from 'vue-router'

/**
 * 路由表。
 *
 * 和 Streamlit 版的多页导航一一对应，只是换成了浏览器路由：切页面不再重跑
 * 整个应用，每个页面还能直接分享链接。
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
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
})
