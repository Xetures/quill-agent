import { test } from '@playwright/test'

import { mockApi } from './mock-api'

/**
 * 【临时】两个前置问题的验证：
 *   1. `visibility: hidden` 会不会把 box-shadow 一起藏掉（决定「纯背景」参照图是否可信）；
 *   2. 四块板的外投影到底有没有出现在窗口 padding 里（决定去掉它会不会损失「浮起来」的观感）。
 * 用完即删。
 */
const PLATES = '.topbar, .sidebar .brand, .sidebar .panel, .chat, .page'

test('基线核实 light', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => localStorage.setItem('quill:theme', 'light'))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.waitForTimeout(1200)

  // A 组：单块孤立元素，测 visibility:hidden 是否藏阴影（用一个大投影放大效果）
  await page.setContent(`
    <style>
      body { margin:0; background:#fff; }
      #x { position:absolute; left:200px; top:200px; width:100px; height:60px;
           background:#fff; box-shadow: 0 8px 28px rgba(0,0,0,0.6); }
    </style>
    <div id="x"></div>
  `)
  await page.waitForTimeout(200)
  await page.screenshot({ path: '/tmp/basecheck-visible.png' })
  await page.addStyleTag({ content: '#x { visibility: hidden; }' })
  await page.waitForTimeout(200)
  await page.screenshot({ path: '/tmp/basecheck-hidden.png' })

  // B 组：真实页面，测 padding 区是否有外投影
  await page.goto('http://localhost:5199/')
  await page.waitForTimeout(1500)
  await page.screenshot({ path: '/tmp/basecheck-n.png' })
  await page.addStyleTag({ content: '#__none__ { }' })
  const h = await page.addStyleTag({ content: `${PLATES} { box-shadow: none !important; }` })
  await page.waitForTimeout(500)
  await page.screenshot({ path: '/tmp/basecheck-noshadow.png' })
  await h.evaluate((el) => el.remove())

  // C 组：只藏板（不改阴影），看 padding 是否变化 —— 判断 visibility 对阴影的作用
  await page.addStyleTag({ content: `${PLATES} { visibility: hidden !important; }` })
  await page.waitForTimeout(600)
  await page.screenshot({ path: '/tmp/basecheck-hiddenreal.png' })
})
