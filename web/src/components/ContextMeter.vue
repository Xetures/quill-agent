<script setup lang="ts">
/**
 * 上下文用量仪表盘：占用 / 窗口，一个占比圆环 + `xxk/xxk` 文字。
 *
 * 两个数字的来源：
 *   - **占用**取最近一条真正发过请求的 assistant 消息的 `stats.prompt_tokens`。
 *     那正是「这一轮发出去的全部上下文」有多大，比数消息条数准确得多。
 *   - **窗口**取当前所选模型**这个模型**的窗口大小（在「API 设置」里配，按模型分开填）。
 *     没填就是 0，显示「—」而不是拿一个默认值硬凑 —— 错的占比比没有占比更糟。
 *
 * 没有可用统计时显示 0 而不是藏起来 —— 面板位置固定，用户能一直看到它，
 * 只是暂时还没有数据。
 */
import { computed } from 'vue'

import { session } from '../stores/session'

/** 从后往前找最后一条带 prompt_tokens 的消息；没有就返回 0。 */
const used = computed(() => {
  for (let i = session.messages.length - 1; i >= 0; i -= 1) {
    const tokens = session.messages[i].stats?.prompt_tokens
    if (tokens) return tokens
  }
  return 0
})

const windowSize = computed(() => {
  const option = session.models.find((item) => item.key === session.modelKey)
  return option?.context_window ?? 0
})

const percent = computed(() => {
  if (!windowSize.value) return 0
  return Math.min(100, Math.round((used.value / windowSize.value) * 100))
})

/**
 * 128000 -> "128k"，1000000 -> "1M"，1224 -> "1.2k"。
 *
 * 满一千就折成 k 是不够的：百万级窗口会显示成「1000k」，而千位取整又会把
 * 1224 显示成「1k」—— 分子本来就小，再把小数抹掉就看不出变化了。
 */
function humanize(tokens: number): string {
  if (tokens >= 1_000_000) return `${trim(tokens / 1_000_000)}M`
  if (tokens >= 1000) return `${trim(tokens / 1000)}k`
  return String(tokens)
}

/** 去掉多余的小数位：整数不留 .0，小数最多保留一位。 */
function trim(value: number): string {
  return value >= 100 || Number.isInteger(value) ? String(Math.round(value)) : value.toFixed(1)
}

const caption = computed(
  () => `${humanize(used.value)}/${windowSize.value ? humanize(windowSize.value) : '—'}`,
)

/** 圆环只有 40px，塞不下百分比文字，所以读数改用悬停提示给出来。 */
const tooltip = computed(() => {
  if (!windowSize.value) return '当前模型没有配置上下文窗口大小（去「API 设置」里填）'
  return `上下文占用 ${percent.value}%（${used.value} / ${windowSize.value} tokens）`
})

/** 越接近上限越该提醒：<70% 主色，70~90% 警告，>90% 危险。 */
const color = computed(() => {
  if (percent.value >= 90) return 'var(--el-color-danger)'
  if (percent.value >= 70) return 'var(--el-color-warning)'
  return 'var(--accent)'
})
</script>

<template>
  <div class="meter" :title="tooltip">
    <!-- 不放环内文字：40px 的圈里塞百分比只会糊成一团。占比看环、读数看右边 -->
    <el-progress
      type="dashboard"
      :percentage="percent"
      :color="color"
      :width="40"
      :stroke-width="4"
      :show-text="false"
    />

    <div class="caption">
      <!-- 等宽字体 + 固定不折行，数字跳动时宽度不抖 -->
      <div class="value mono">{{ caption }}</div>
      <div class="unit muted">上下文</div>
    </div>
  </div>
</template>

<style scoped>
/* 紧凑的一小格，贴着功能区那一行的最右边（位置由调用方的 .meter-slot 决定） */
.meter {
  display: flex;
  align-items: center;
  gap: 8px;
}

.caption {
  line-height: 1.25;
}

.value {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.unit {
  font-size: 11px;
}

/* 窄窗口先舍掉文字，保留圆环 —— 环本身就能看出占比，文字进来还得折行 */
@media (max-width: 720px) {
  .caption {
    display: none;
  }
}
</style>
