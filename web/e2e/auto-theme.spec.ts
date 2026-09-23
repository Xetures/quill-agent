import { expect, test } from '@playwright/test'

import { mockApi } from './mock-api'

/**
 * 「跟随系统」这件事的落点。
 *
 * 容易坏的地方：系统一变，主题该换到用户**自己选的那套**（比如深色用极光），
 * 而不是永远退回最基础的靛蓝 —— 后者曾经是真实的写法。这里把它钉住。
 */
test('跟随系统：系统深色时用用户选的那一套', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    localStorage.setItem('quill:theme', 'auto')
    localStorage.setItem('quill:auto-dark', 'aurora')
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await page.waitForTimeout(800)

  const theme = await page.evaluate(() => document.documentElement.dataset.theme)
  expect(theme).toBe('aurora')
})

test('跟随系统：系统浅色时用用户选的那一套', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    localStorage.setItem('quill:theme', 'auto')
    localStorage.setItem('quill:auto-light', 'sakura')
  })
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  await page.waitForTimeout(800)

  const theme = await page.evaluate(() => document.documentElement.dataset.theme)
  expect(theme).toBe('sakura')
})

/** 没配过那两套时，退回最基础的两档（晴空 / 靛蓝）。 */
test('跟随系统：没配过就退回基础两档', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    localStorage.setItem('quill:theme', 'auto')
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await page.waitForTimeout(800)

  const theme = await page.evaluate(() => document.documentElement.dataset.theme)
  expect(theme).toBe('dark')
})
