import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [
    vue(),

    // Element Plus 按需引入。
    //
    // 两个插件分工不同，都要配：
    //   AutoImport —— 自动导入「函数式 API」（ElMessage、ElMessageBox 等）
    //   Components —— 自动注册模板里的 <el-xxx> 标签
    //
    // 好处是代码里直接写 `<el-button>` 和 `ElMessage.success(...)`，
    // 既不用手写 import，产物里也只包含真正用到的组件和样式。
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  server: {
    port: 5173,
    // 开发时把 /api 转发给 FastAPI。
    // 前端代码里一律写相对路径 /api/xxx —— 不用管后端地址，也没有跨域问题：
    // 浏览器看到的始终是同源请求。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
