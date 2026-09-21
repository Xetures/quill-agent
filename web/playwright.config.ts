import { defineConfig } from '@playwright/test'

/**
 * 前端冒烟测试。
 *
 * **只打前端**：所有 `/api/*` 都在测试里用 `page.route` 拦掉（见 `e2e/mock-api.ts`），
 * 所以不需要后端、也不需要模型 —— 快且稳定。后端已经有几百个单测覆盖，这里要的是
 * 「界面 + SSE 解析 + 渲染」这条链路的回归保护。
 *
 * 为什么现在才补：这条链路以前**完全没有自动化**，全靠手工在浏览器里点。
 * 2026-09 排查「界面卡死」时就是一路手工点过来的，中途还几次被过期的快照文件误导，
 * 绕了不少弯路 —— 有这几条用例，至少关键路径的状态能被自动拦住。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5199',
    // 本地用系统已装的 Chrome，省掉 `npx playwright install` 那几百 MB 下载；
    // CI 上没有系统 Chrome，交给 Playwright 自带的 chromium（先跑一次 install chromium，见 ci.yml）
    ...(process.env.CI ? {} : { channel: 'chrome' }),
  },
  webServer: {
    // 用一个独立端口，别去碰用户可能正开着的 5173。
    // 测试的 `/api/*` 全被 route 拦下，所以这个 dev server 的代理指向哪都无所谓。
    command: 'npm run dev -- --port 5199 --strictPort',
    url: 'http://localhost:5199',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
