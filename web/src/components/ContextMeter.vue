<script setup lang="ts">
/**
 * 上下文用量仪表盘：占用 / 窗口，一个占比圆环 + `xxk/xxk` 文字。
 *
 * 读数从哪来：
 *   - **运行中**优先用流里播报的实时读数（`session.liveUsage`）。一轮里模型会被请求
 *     多次（每执行完一轮工具再问一次），上下文一路在长；每拿到一次用量就刷新一次，
 *     所以读数是**跟上去**的，而不是全程停在上一轮、跑完才跳一下。
 *   - **没在跑**（或服务端不发用量）时退回读最近一条真正发过请求的 assistant 消息的
 *     `stats.context_tokens` —— 它和最后一次播报本来就是同一个值。
 *     这两处都不能读 `prompt_tokens`：一轮里每请求一次就要把同一份上下文重发一遍，
 *     那个字段是累计的账单，一个读了十来个文件的轮次能把它累到真实的十倍以上。
 *   - **窗口**取当前所选模型**这个模型**的窗口大小（在「API 设置」里配，按模型分开填）。
 *     没填就是 0，显示「—」而不是拿一个默认值硬凑 —— 错的占比比没有占比更糟。
 *
 * 没有可用统计时显示 0 而不是藏起来 —— 面板位置固定，用户能一直看到它，
 * 只是暂时还没有数据。
 */
import { computed } from 'vue'

import { session } from '../stores/session'

/**
 * 从后往前找最后一条有上下文读数的消息；没有就返回 0。
 *
 * 退回 `prompt_tokens` 只对「加 `context_tokens` 之前落盘的老记录」生效 ——
 * 那些记录里最后一次请求的输入量没被记下来，只能拿累计值顶上（偏高，
 * 但比显示 0 更像「有数据」）。发一条新消息就会恢复正常。
 */
function fromMessages(): number {
  for (let i = session.messages.length - 1; i >= 0; i -= 1) {
    const stats = session.messages[i].stats
    if (!stats) continue

    const tokens = stats.context_tokens || stats.prompt_tokens
    if (tokens) return tokens
  }
  return 0
}

/**
 * 运行中播报的读数优先：它比消息上的 `stats` 新。
 *
 * 消息里的 `stats` 要等这一轮落盘（`done`）才有 —— 只读它的话，整个跑的过程中读数
 * 都停在上一轮，跑完才跳一下。运行中的每个 `usage` 事件都给一个当时的上下文大小，
 * 所以这个数是一路跟着长上去的。
 */
const used = computed(() => session.liveUsage || fromMessages())

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
