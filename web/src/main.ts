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

createApp(App).use(router).mount('#app')
