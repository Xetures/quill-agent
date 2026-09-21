<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api/client'
import quillIconLarge from '../assets/quill-icon-large.svg'
import { setTheme, themeMode, type ThemeMode } from '../stores/theme'

const OPTIONS: { value: ThemeMode; label: string; hint: string }[] = [
  { value: 'light', label: '浅色', hint: '米白纸感' },
  { value: 'dark', label: '深色', hint: '靛蓝夜色' },
  { value: 'auto', label: '跟随系统', hint: '随系统的深浅色设置自动切换' },
]

const version = ref('')

/**
 * 单轮对话的开销上限（token 数的字符串形式）。
 *
 * 存成字符串是因为偏好文件里全是字符串（见后端 `preferences.py`），
 * 空串 = 不限制 —— 这样「没配过」和「配了 0」是同一种状态，不用分两套判断。
 */
const MAX_RUN_TOKENS_KEY = 'max_run_tokens'
const maxRunTokens = ref('')

/**
 * 单轮最多跑几轮工具调用（字符串形式）。
 *
 * 它是**兜底**，不是主闸门 —— 真正管住成本的是上面那个 token 上限，真跑飞了
 * 该先撞上预算。之所以还留个口子：任务的「正常轮次」差得很远，探路型任务
 * （摸结构 → 读文档 → 确认工具链 → 动手）十几轮才够。空串 = 用后端默认值。
 */
const MAX_ITERATIONS_KEY = 'max_iterations'
const maxIterations = ref('')

onMounted(async () => {
  // 版本号只有探活接口有。拿不到就不显示，不值得为它加一个专门的端点
  try {
    const data = await api.get<{ version: string }>('/health')
    version.value = data.version
  } catch {
    version.value = ''
  }

  // 键不存在就留空（= 不限制），不写默认值回文件 —— 用户没配过的东西不该被我们改
  try {
    const prefs = await api.get<Record<string, string>>('/preferences')
    maxRunTokens.value = prefs[MAX_RUN_TOKENS_KEY] ?? ''
    maxIterations.value = prefs[MAX_ITERATIONS_KEY] ?? ''
  } catch {
    maxRunTokens.value = ''
    maxIterations.value = ''
  }
})

async function saveBudget(): Promise<void> {
  // 空串照传：后端把它当作「不限制」。**不能跳过保存** —— 那用户就没法把
  // 已经设过的上限改回不限制了
  await api.put('/preferences', {
    values: { [MAX_RUN_TOKENS_KEY]: maxRunTokens.value.trim() },
  })
}

async function saveMaxIterations(): Promise<void> {
  // 同上：空串照传（后端当作「用默认值」），这样改过的值还能改回默认
  await api.put('/preferences', {
    values: { [MAX_ITERATIONS_KEY]: maxIterations.value.trim() },
  })
}
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>偏好设置</h1>
      <span class="hint">界面外观等只影响本地界面的选项</span>
    </Teleport>

    <section class="group">
      <h2>主题</h2>
      <p class="muted desc">
        选一套配色。选「跟随系统」的话，系统切换深浅色时这里会跟着变。
      </p>

      <div class="themes">
        <button
          v-for="item in OPTIONS"
          :key="item.value"
          type="button"
          class="theme-card"
          :class="{ active: themeMode === item.value }"
          @click="setTheme(item.value)"
        >
          <!-- 用两个色块示意「侧边栏 + 主区」，比放一张真截图轻，换主题时也不用维护 -->
          <span class="preview" :class="item.value">
            <span class="preview-side" />
            <span class="preview-main" />
          </span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>

    <section class="group">
      <h2>对话</h2>
      <p class="muted desc">
        单轮对话最多花多少 token。超过就当场停下 —— 一轮里模型可能被请求很多次，
        光靠「最多几步」管不住花了多少钱。留空或填 0 表示不限制。
      </p>

      <div class="field">
        <el-input
          v-model="maxRunTokens"
          class="budget-input"
          placeholder="例如 200000"
          @change="saveBudget"
        />
        <span class="muted unit">token / 轮</span>
      </div>

      <p class="muted desc sub">
        单轮最多跑几轮工具调用。它是个兜底 —— 真跑飞了，该先撞上的是上面那个预算。
        留空用默认值（30）。任务的正常轮次差得很远：探路型任务（摸清结构、读文档、
        确认工具链）十几轮才够，所以留了这个口子。
      </p>

      <div class="field">
        <el-input
          v-model="maxIterations"
          class="budget-input"
          placeholder="留空用默认（30）"
          @change="saveMaxIterations"
        />
        <span class="muted unit">轮 / 单轮对话</span>
      </div>
    </section>

    <section class="group">
      <h2>关于</h2>
      <div class="app-info">
        <!-- 大尺寸图标（1024，带羽毛细节的那版）：这里放得下，也就只有这里用得到 -->
        <img class="app-icon" :src="quillIconLarge" alt="" />
        <div>
          <div class="app-name">quill</div>
          <div class="muted app-desc">本地 Agent 工作台{{ version ? ` · v${version}` : '' }}</div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.group {
  max-width: 640px;
}

.group + .group {
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
}

.field {
  display: flex;
  align-items: center;
  gap: 10px;
}

.budget-input {
  width: 200px;
}

.unit {
  font-size: 12px;
}

.app-info {
  display: flex;
  align-items: center;
  gap: 14px;
}

.app-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
}

.app-name {
  font-size: 15px;
  font-weight: 600;
}

.app-desc {
  font-size: 12px;
}

h2 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
}

.desc {
  margin: 0 0 14px;
  font-size: 13px;
}

/* 同一节里的第二项：拉开一点，免得两段说明糊在一起 */
.desc.sub {
  margin-top: 20px;
}

.themes {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.theme-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 10px;
  width: 148px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease,
    transform 0.15s ease;
}

.theme-card:hover {
  border-color: var(--accent);
  box-shadow: var(--shadow);
}

.theme-card.active {
  border-color: var(--accent);
  /* 选中态用主色描边 + 一圈柔光，比换背景色克制 —— 卡片里已经有预览色块了 */
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.preview {
  position: relative;
  display: block;
  width: 100%;
  height: 56px;
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.preview-side {
  position: absolute;
  inset: 0 auto 0 0;
  width: 30%;
}

.preview-main {
  position: absolute;
  inset: 0 0 0 30%;
}

/* 三种预览各用一组写死的颜色 —— 这里刻意不用主题变量：
 * 卡片要展示的正是「另一套主题长什么样」，跟着当前主题走就没意义了 */
.preview.light .preview-side {
  background: #f3ecdf;
}
.preview.light .preview-main {
  background: #faf6ee;
}

.preview.dark .preview-side {
  background: #152836;
}
.preview.dark .preview-main {
  background: #0f1e2b;
}

.preview.auto .preview-side {
  background: linear-gradient(160deg, #152836 50%, #f3ecdf 50%);
}
.preview.auto .preview-main {
  background: linear-gradient(160deg, #0f1e2b 50%, #faf6ee 50%);
}

.label {
  font-size: 13px;
  font-weight: 500;
}

.theme-card .hint {
  font-size: 11px;
  color: var(--text-soft);
  line-height: 1.4;
}
</style>
