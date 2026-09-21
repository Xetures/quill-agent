import { expect, test } from '@playwright/test'

import { mockApi } from './mock-api'

/**
 * 逐页冒烟：每个页面都能打开、标题正确、控制台不报错。
 *
 * 等价于把「手工把 11 个页面点一遍」自动化 —— 2026-09 那次功能巡检就是这么做的，
 * 手工做一次还行，但没法每次都做。这里盯住的是最廉价的回归：某个页面引用了
 * 已经不存在的接口或字段（前后端契约漂移），一打开就报错。
 */
const PAGES = [
  { path: '/', title: '任务' },
  { path: '/modes', title: '模式' },
  { path: '/prompts', title: '提示词' },
  { path: '/models', title: 'API 设置' },
  { path: '/search', title: '联网搜索' },
  { path: '/tools', title: '工具' },
  { path: '/skills', title: '技能' },
  { path: '/memory', title: '记忆' },
  { path: '/archived', title: '归档' },
  { path: '/usage', title: '用量' },
  { path: '/settings', title: '偏好设置' },
] as const

for (const { path, title } of PAGES) {
  test(`页面 ${path} 能打开且不报错`, async ({ page }) => {
    const errors: string[] = []
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(m.text())
    })
    page.on('pageerror', (e) => errors.push(e.message))

    await mockApi(page)
    await page.goto(path)

    // 标题由各页 Teleport 到顶栏（见 README 7.9），用它断言这一页确实起来了
    await expect(page.locator('h1').first()).toHaveText(title, { timeout: 10_000 })
    expect(errors, `控制台报错：\n${errors.join('\n')}`).toEqual([])
  })
}
