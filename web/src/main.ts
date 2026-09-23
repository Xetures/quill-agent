import { createApp } from 'vue'

// Element Plus 的暗色变量。它只定义 `html.dark` 下的一组 CSS 变量，代价很小，
// 所以不管当前用不用深色都先引进来。必须在 style.css **之前**：下面那个文件
// 会用同一批选择器覆盖它
import 'element-plus/theme-chalk/dark/css-vars.css'

import App from './App.vue'
import { router } from './router'
import './style.css'
// 只为副作用而 import：主题在模块加载时就会挂到 <html> 上（见 stores/theme.ts）
import './stores/theme'

/**
 * 懒加载的代码块加载失败时，整页重载一次。
 *
 * 典型场景：页面已经开着，后端这边重新构建了前端 —— 新的产物文件名带着新 hash，
 * 旧的那些已经被删掉。老页面里还引用着旧文件，用户一点到「某个还没加载过的页面 /
 * 组件」就会 404。
 *
 * Vite 为此专门派发这个事件（官方推荐的兜底写法）。刷新一次就能回到新版本，
 * 比让用户对着一个「看着还在、点着没反应」的界面强。
 */
window.addEventListener('vite:preloadError', () => {
  window.location.reload()
})

// 桌面壳（pywebview）打开的窗口会带 `?desktop=1`：那边把窗口内容延伸进了系统
// 标题栏，红绿灯浮在窗口左上角 —— 界面据此从标题栏高度开始画，把那一条让出来
// （样式见 App.vue 里 `:global(html.in-desktop)` 那段）。
// 浏览器里没有这个参数，一切照旧
if (new URLSearchParams(window.location.search).has('desktop')) {
  document.documentElement.classList.add('in-desktop')
}

createApp(App).use(router).mount('#app')
