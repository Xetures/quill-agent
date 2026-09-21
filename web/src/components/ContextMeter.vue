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
 * 从后往前找最后一条有统计的消息，取出「上下文读数 + 缓存命中数」。
 *
 * 退回 `prompt_tokens` 只对「加 `context_tokens` 之前落盘的老记录」生效 ——
 * 那些记录里最后一次请求的输入量没被记下来，只能拿累计值顶上（偏高，
 * 但比显示 0 更像「有数据」）。发一条新消息就会恢复正常。
 *
 * 两个数**必须来自同一条消息**（同一次请求）：跨记录取的话，命中率会变成一个
 * 分子分母不同源的比率 —— 看着像那么回事，其实没有意义。
 */
function fromMessages(): { used: number; cached: number } {
  for (let i = session.messages.length - 1; i >= 0; i -= 1) {
    const stats = session.messages[i].stats
    if (!stats) continue

    const used = stats.context_tokens || stats.prompt_tokens
    if (used) return { used, cached: stats.cached_tokens ?? 0 }
  }
  return { used: 0, cached: 0 }
}

/**
 * 运行中播报的读数优先：它比消息上的 `stats` 新。
 *
 * 消息里的 `stats` 要等这一轮落盘（`done`）才有 —— 只读它的话，整个跑的过程中读数
 * 都停在上一轮，跑完才跳一下。运行中的每个 `usage` 事件都给一个当时的上下文大小，
 * 所以这个数是一路跟着长上去的。
 */
const used = computed(() => session.liveUsage || fromMessages().used)

/**
 * 缓存命中的 token 数；和 `used` **取同一个来源**（运行中用播报的那份，
 * 否则用消息里的那份）。两条来源都必须是同一次请求的数据，否则比率没有意义。
 */
const cached = computed(() => (session.liveUsage ? session.liveCached : fromMessages().cached))

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

/**
 * 缓存命中率（百分比）；**还没有可用读数时返回 null** —— 界面据此整块不显示，
 * 而不是填一个 0%。「不知道」和「真的没命中」是两回事，摆个 0 会被当成后者。
 *
 * 分母就是 `used`（同一次请求的输入量）—— 只有这样，这个百分比和旁边的上下文
 * 读数才对得上。
 */
const hitRate = computed(() => {
  if (!used.value) return null
  return Math.round((cached.value / used.value) * 100)
})

/** 命中率的悬停说明：把分子分母都摆出来，用户才知道这个数是怎么来的。 */
const cacheTooltip = computed(() => {
  if (hitRate.value === null) return ''
  return (
    `缓存命中率 ${hitRate.value}%（${cached.value} / ${used.value} tokens）\n` +
    '命中部分是这次请求复用的前缀 —— 越高越省钱、也越快。\n' +
    '服务商不返回这项数据时显示 0%。'
  )
})

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
    <!-- 缓存命中率，贴在上下文读数的**左边**。它和上下文占用读的是同一次请求的数据，
         放一起才看得出「这份上下文里有多少是复用来的」 -->
    <div v-if="hitRate !== null" class="cache" :title="cacheTooltip">
      <span class="value mono">{{ hitRate }}%</span>
      <span class="unit muted">缓存</span>
    </div>

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

/* 缓存命中率（见模板）。和右边的读数一样是「数字 + 小字单位」两段，
   但排成一行：它只是补充信息，占两行会把整块撑高 */
.cache {
  display: flex;
  align-items: baseline;
  gap: 4px;
  white-space: nowrap;
  cursor: default;
}

.cache .value {
  font-size: 13px;
  font-weight: 600;
}

.cache .unit {
  font-size: 11px;
}

/* 窄窗口先舍掉文字，保留圆环 —— 环本身就能看出占比，文字进来还得折行 */
@media (max-width: 720px) {
  .caption,
  .cache {
    display: none;
  }
}
</style>
