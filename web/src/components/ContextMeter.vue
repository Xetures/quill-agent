<script setup lang="ts">
/**
 * 上下文用量读数：占用 / 窗口，一行 `xxk/xxk` 文字；占比走悬停提示。
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
import { useI18n } from 'vue-i18n'

import { session } from '../stores/session'

const { t } = useI18n()

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
 * 缓存命中率（百分比）；**还没有可用读数时返回 null**。
 *
 * null 时界面显示「—」而**不是 0%**：「不知道」和「真的没命中」是两回事，
 * 摆个 0 会被当成后者。但这一格**始终占着位置** —— 藏起来的话，功能区最右边会随着
 * 「有没有会话、这一轮跑没跑过」忽有忽无，旁边那两处读数跟着来回跳。
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
  if (hitRate.value === null) {
    return t('contextMeter.noReading')
  }
  return (
    `${t('contextMeter.cacheHit', { rate: hitRate.value, cached: cached.value, used: used.value })}\n` +
    `${t('contextMeter.cacheHitHint')}\n` +
    t('contextMeter.cacheHitMissing')
  )
})

/**
 * 界面上只摆得下「占用 / 窗口」这行读数，占多少百分比交给悬停提示 ——
 * 它是个确切的数（`上下文占用 37%（48k / 128k tokens）`），比一个看不出刻度的圈准。
 */
const tooltip = computed(() => {
  if (!windowSize.value) return t('contextMeter.noWindow')
  return t('contextMeter.usage', { percent: percent.value, used: used.value, size: windowSize.value })
})
</script>

<template>
  <!-- 两处读数（缓存命中率、上下文占用）是**平级排布**的，见下面 .meter 那段样式说明 -->
  <div class="meter">
    <!-- 缓存命中率，贴在上下文读数的**左边**。它和上下文占用读的是同一次请求的数据，
         放一起才看得出「这份上下文里有多少是复用来的」。
         这一格**常驻**（没有读数时显示「—」）：藏起来会让整排读数随着
         「有没有会话、这一轮跑没跑过」忽有忽无，旁边那处跟着来回跳 -->
    <div class="cache" :title="cacheTooltip">
      <span class="value mono">{{ hitRate === null ? '—' : `${hitRate}%` }}</span>
      <span class="unit muted">{{ t('contextMeter.cacheLabel') }}</span>
    </div>

    <!-- 悬停说明挂在这一格上（见 tooltip）：外层 .meter 不生成盒子，挂它上面悬不到 -->
    <div class="caption" :title="tooltip">
      <!-- 等宽字体 + 固定不折行，数字跳动时宽度不抖 -->
      <div class="value mono">{{ caption }}</div>
      <div class="unit muted">{{ t('contextMeter.contextLabel') }}</div>
    </div>
  </div>
</template>

<style scoped>
/*
 * 两处读数平时抱成一团（下面的 flex），**整排放得下时才拆成平级项**（见下面那条媒体查询）。
 *
 * 为什么宽窗口要拆：作为一个 flex 容器，两处读数之间是这里的 `gap`（8px），而外面那一排
 * 是 `justify-content: space-between` 均分出来的（1280 宽的窗口下 48px）—— 「缓存」
 * 右边 8px、左边 48px，看着就是往右偏的。
 *
 * 为什么窄窗口不拆：`.tools` 是 `flex-wrap: wrap`，窗口一窄整排就要换行；拆成平级项之后
 * 这两处可能被甩到同一行的两端（实测 900 宽时中间空 595px），比挨在一起难读得多。
 */
.meter {
  display: flex;
  align-items: center;
  gap: 8px;
}

.caption {
  line-height: 1.25;
  /* 成了 .tools 的 flex 项之后别被压窄：里头是 nowrap 的数字，压了会被裁掉 */
  flex-shrink: 0;
}

.value {
  font-size: var(--fs-sm);
  font-weight: 600;
  white-space: nowrap;
}

.unit {
  font-size: var(--fs-2xs);
}

/* 缓存命中率（见模板）。和右边的读数一样是「数字 + 小字单位」两段，
   但排成一行：它只是补充信息，占两行会把整块撑高 */
.cache {
  display: flex;
  align-items: baseline;
  gap: 4px;
  /* 和 .caption 一样：成了 .tools 的 flex 项之后别被压窄 */
  flex-shrink: 0;
  white-space: nowrap;
  cursor: default;
}

.cache .value {
  font-size: var(--fs-sm);
  font-weight: 600;
}

.cache .unit {
  font-size: var(--fs-2xs);
}

/*
 * 整排放得下时把两处读数拆成平级项：`display: contents` 让这一层不生成盒子，
 * .cache / .caption 直接变成 .tools 的兄弟，跟着整排被 `space-between` 均分 ——
 * 「缓存」左右两边的空隙这才一样宽。
 *
 * 代价是这一层自己接不住鼠标（它没有盒子），所以悬停说明挂在读数上（见模板）。
 *
 * 断点只是个粗略的界：整排到底换不换行还取决于工作目录那一格有多宽（它显示的是路径，
 * 路径长就宽），所以宁可取宽一点 —— 取窄了会撞上「两处被甩到一行两端」那种排布。
 */
@media (min-width: 1100px) {
  .meter {
    display: contents;
  }
}
</style>
