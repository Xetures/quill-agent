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

onMounted(async () => {
  // 版本号只有探活接口有。拿不到就不显示，不值得为它加一个专门的端点
  try {
    const data = await api.get<{ version: string }>('/health')
    version.value = data.version
  } catch {
    version.value = ''
  }
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>设置</h1>
      <span class="hint">界面外观</span>
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
