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
    // 联网搜索紧接着 API 设置：它也是一份「外部服务的地址 + Key」，
    // 只是服务的是搜索而不是模型
    { path: '/search', name: 'search', component: () => import('./views/SearchView.vue') },
    { path: '/tools', name: 'tools', component: () => import('./views/ToolsView.vue') },
    { path: '/skills', name: 'skills', component: () => import('./views/SkillsView.vue') },
    { path: '/memory', name: 'memory', component: () => import('./views/MemoryView.vue') },
    { path: '/archived', name: 'archived', component: () => import('./views/ArchivedView.vue') },
    // 用量是「回来的看」的页面，排在归档之后、设置之前
    { path: '/usage', name: 'usage', component: () => import('./views/UsageView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
})
