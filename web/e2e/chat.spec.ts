import { expect, test } from '@playwright/test'

import { doneMessage, mockApi, sse } from './mock-api'

/**
 * 任务页的冒烟测试：发消息 → 渲染 → 收尾。
 *
 * 这三条对应的是最容易出问题、也最难手工穷尽的几处状态流转：
 * 流式正文 + 工具步骤渲染、请求失败后的解锁、运行中的输入禁用。
 */

test('正常一轮：正文与工具步骤渲染出来，结束后输入框解锁', async ({ page }) => {
  const step = {
    name: 'read_file',
    arguments: '{"path": "README.md"}',
    result: '……',
    elapsed: 0.003,
  }

  await mockApi(page, {
    chat: () => ({
      status: 200,
      body:
        sse('start', { run_id: 'run-1' }) +
        sse('text', { text: '我来看一下。' }) +
        sse('tool', step) +
        sse('text', { text: '这个项目是 quill。' }) +
        sse('done', {
          message: doneMessage({
            content: '我来看一下。这个项目是 quill。',
            steps: [step],
            stats: {
              prompt_tokens: 80,
              completion_tokens: 20,
              total_tokens: 100,
              context_tokens: 90,
              elapsed: 1.5,
            },
          }),
        }),
    }),
  })

  await page.goto('/')

  const input = page.locator('textarea')
  await input.fill('这个项目是做什么的？')
  await page.locator('button[title="发送"]').click()

  // 用户消息上屏
  await expect(page.getByText('这个项目是做什么的？')).toBeVisible()
  // 工具步骤（可折叠）出现，并带上耗时
  await expect(page.getByRole('button', { name: /read_file/ })).toBeVisible()
  // 助手正文
  await expect(page.getByText(/这个项目是 quill/).first()).toBeVisible()
  // 跑完了就该能接着输入 —— 卡在 busy 正是这次要防的回归
  await expect(input).toBeEnabled()
})

test('运行中：显示「停止这一轮」，输入框禁用', async ({ page }) => {
  // 让 /api/chat 迟迟不回，制造一个「正在跑」的中间态。
  // route.fulfill 一次给完整 body，没法真的流式，所以用延迟来模拟
  await mockApi(page, {
    chat: async () => {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      return {
        status: 200,
        body: sse('done', { message: doneMessage({ content: '好了。' }) }),
      }
    },
  })

  await page.goto('/')

  const input = page.locator('textarea')
  await input.fill('在吗')
  await page.locator('button[title="发送"]').click()

  await expect(page.locator('button[title="停止这一轮"]')).toBeVisible()
  await expect(input).toBeDisabled()
})

test('请求失败：给出提示并解锁输入框（不能卡在 busy）', async ({ page }) => {
  await mockApi(page, { chat: () => ({ status: 500, body: 'boom' }) })

  await page.goto('/')

  const input = page.locator('textarea')
  await input.fill('你好')
  await page.locator('button[title="发送"]').click()

  // 失败要以提示的形式落到消息流里，而不是静默
  await expect(page.getByText(/请求失败/)).toBeVisible()
  // 出错也必须解锁 —— 否则用户只能刷新页面
  await expect(input).toBeEnabled()
})
