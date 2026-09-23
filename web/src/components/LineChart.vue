<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 平滑折线图。手写 SVG，不引图表库。
 *
 * 为什么不引 echarts / chart.js：这张图的形状是固定的（几个日期点、几条线），
 * 而引一个库要给产物添几百 KB，还得在运行时把 CSS 变量读出来再喂给它，主题
 * 才能跟着切。手写反而更短：颜色直接写 `var(--chart-n)`，跟着主题走，
 * 一行适配代码都不用加。
 */

interface Series {
  name: string
  values: number[]
}

const props = defineProps<{
  /** 横轴标签，与每条 series 的 values 一一对应。 */
  labels: string[]
  series: Series[]
}>()

/** 画布的宽与高都跟着容器走（见下面的 ResizeObserver）。 */
const PAD = { top: 16, right: 16, bottom: 30, left: 52 }

/** 调色板的色号个数，和 style.css 里的 --chart-n 对齐。 */
const COLORS = 6

/** 图表区（图例以下那块）。宽高都从它身上量。 */
const plotBox = ref<HTMLElement | null>(null)
const width = ref(760)
const height = ref(240)
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!plotBox.value) return

  // 量容器：SVG 用 viewBox 缩放会把字和线一起拉变形，所以按真实像素画。
  // 高度也一起量 —— 窗口一高，图就该跟着高（原先写死 240px，面板一矮就被裁掉一截）
  observer = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect
    if (!rect) return
    if (rect.width) width.value = rect.width
    // 下限 120：再矮也画不出有意义的东西；上限不设，交给窗口自己定
    if (rect.height) height.value = Math.max(120, Math.round(rect.height))
  })
  observer.observe(plotBox.value)
})

onBeforeUnmount(() => observer?.disconnect())

const plotWidth = computed(() => Math.max(width.value - PAD.left - PAD.right, 10))
const plotHeight = computed(() => Math.max(height.value - PAD.top - PAD.bottom, 10))

/** 纵轴上每格可能的步长（1 和 2.5 都在里面：步长必须看起来是个整数）。 */
const NICE_STEPS = [1, 2, 2.5, 3, 4, 5, 6, 8, 10]

/**
 * 纵轴上限，取一个「好看的整数」。
 *
 * 直接拿峰值当上限的话，刻度会变成 3721 这种读不出来的数；折半找步长又会让
 * 曲线只占半屏。这里的做法是先估一个粗步长，再向上取到最近的整步长，最后
 * 乘以格数 —— 峰值永远贴着顶格，刻度也都是整数。
 */
const ceiling = computed(() => {
  const peak = Math.max(0, ...props.series.flatMap((item) => item.values))
  if (peak <= 4) return 4 // 步长至少 1，免得出现「0.25 token」这种刻度

  const rough = peak / 4
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const normalized = rough / magnitude
  const step = (NICE_STEPS.find((item) => normalized <= item) ?? 10) * magnitude

  return Math.max(step, 1) * 4
})

const yTicks = computed(() =>
  Array.from({ length: 5 }, (_, index) => {
    const value = (ceiling.value / 4) * index
    const y = PAD.top + plotHeight.value - (value / ceiling.value) * plotHeight.value
    return { value, y }
  }),
)

const xOf = (index: number): number => {
  const count = props.labels.length
  if (count <= 1) return PAD.left + plotWidth.value / 2
  return PAD.left + (plotWidth.value * index) / (count - 1)
}

const yOf = (value: number): number =>
  PAD.top + plotHeight.value - (value / ceiling.value) * plotHeight.value

interface Drawable {
  name: string
  color: string
  path: string
  points: { x: number; y: number; value: number }[]
}

const drawables = computed<Drawable[]>(() =>
  props.series.map((item, order) => {
    // 值比标签短时补 0：数据缺一段也好过整张图错位
    const values = props.labels.map((_, index) => item.values[index] ?? 0)
    const points = values.map((value, index) => ({
      x: xOf(index),
      y: yOf(value),
      value,
    }))

    return {
      name: item.name,
      color: `var(--chart-${(order % COLORS) + 1})`,
      path: smoothPath(points),
      points,
    }
  }),
)

const round = (value: number): number => Math.round(value * 100) / 100

/**
 * 平滑曲线：单调三次插值（Fritsch–Carlson）。
 *
 * 不用「控制点取中点」那条最常见的路子：它会在数据贴近 0 时把线甩到轴下面去
 * （过冲），而 token 用量出现负数是说不通的。单调插值保证了相邻两点之间不
 * 额外产生极值，既平滑又不会跌出边界。
 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (!points.length) return ''
  if (points.length === 1) return `M ${round(points[0].x)} ${round(points[0].y)}`

  const count = points.length
  const spans: number[] = []
  const slopes: number[] = []

  for (let index = 0; index < count - 1; index += 1) {
    const span = points[index + 1].x - points[index].x
    spans.push(span)
    slopes.push(span === 0 ? 0 : (points[index + 1].y - points[index].y) / span)
  }

  const tangents: number[] = [slopes[0]]
  for (let index = 1; index < count - 1; index += 1) {
    // 相邻斜率反号说明这里是极值点，切线取水平，曲线才不会冲出去
    const flat = slopes[index - 1] * slopes[index] <= 0
    tangents.push(flat ? 0 : (slopes[index - 1] + slopes[index]) / 2)
  }
  tangents.push(slopes[count - 2])

  let path = `M ${round(points[0].x)} ${round(points[0].y)}`
  for (let index = 0; index < count - 1; index += 1) {
    const third = spans[index] / 3
    const c1x = points[index].x + third
    const c1y = points[index].y + tangents[index] * third
    const c2x = points[index + 1].x - third
    const c2y = points[index + 1].y - tangents[index + 1] * third
    const end = points[index + 1]

    path += ` C ${round(c1x)} ${round(c1y)} ${round(c2x)} ${round(c2y)} ${round(end.x)} ${round(end.y)}`
  }

  return path
}

// ---------- 悬停 ----------

/** 鼠标落在第几个数据点上；-1 表示不在图里。 */
const hoverIndex = ref(-1)

function onMove(event: MouseEvent): void {
  const rect = (event.currentTarget as SVGRectElement).getBoundingClientRect()
  const count = props.labels.length
  if (count <= 1) {
    hoverIndex.value = 0
    return
  }

  // 点之间是等距的，直接按比例算下标再取整就是最近的那个
  const ratio = (event.clientX - rect.left) / Math.max(rect.width, 1)
  hoverIndex.value = Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))))
}

const tooltip = computed(() => {
  if (hoverIndex.value < 0) return null

  return {
    label: props.labels[hoverIndex.value] ?? '',
    rows: drawables.value.map((item) => ({
      name: item.name,
      color: item.color,
      value: item.points[hoverIndex.value]?.value ?? 0,
    })),
  }
})

/** 靠右半区时把提示框翻到光标左侧，免得被画布边界裁掉。 */
const tooltipStyle = computed(() => {
  const x = hoverIndex.value < 0 ? 0 : xOf(hoverIndex.value)
  const flip = x > width.value / 2

  return {
    left: `${Math.round(x + (flip ? -12 : 12))}px`,
    transform: flip ? 'translateX(-100%)' : 'none',
  }
})

const guideX = computed(() => (hoverIndex.value < 0 ? 0 : xOf(hoverIndex.value)))

// ---------- 展示 ----------

/** 纵轴刻度：过千折成 k / M，刻度文本才不至于挤成一坨。 */
function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${round(value / 1_000_000)}M`
  if (value >= 1000) return `${round(value / 1000)}k`
  return String(round(value))
}

/** 横轴只留月日：7 个「2026-09-19」并排会糊成一片。 */
function shortLabel(label: string): string {
  return label.slice(5) || label
}
</script>

<template>
  <div class="chart">
    <!-- 图例：颜色和折线一一对应。模型名可能很长，所以让它折行 -->
    <div class="legend">
      <span v-for="item in drawables" :key="item.name" class="legend-item">
        <i class="dot" :style="{ background: item.color }" />
        {{ item.name }}
      </span>
    </div>

    <!-- 图表区：吃掉图例之外的剩余高度。svg 按这里量出来的真实像素画（见 script），
         所以窗口一高图就跟着高 —— 它不是一张固定高度的图 -->
    <div ref="plotBox" class="plot">
      <svg :width="width" :height="height" role="img">
        <!-- 横向网格 + 纵轴刻度 -->
        <g v-for="tick in yTicks" :key="tick.value">
          <line :x1="PAD.left" :x2="width - PAD.right" :y1="tick.y" :y2="tick.y" class="grid" />
          <text :x="PAD.left - 8" :y="tick.y + 4" class="axis" text-anchor="end">
            {{ formatTokens(tick.value) }}
          </text>
        </g>

        <!-- 横轴日期 -->
        <text
          v-for="(label, index) in labels"
          :key="label"
          :x="xOf(index)"
          :y="height - 10"
          class="axis"
          text-anchor="middle"
        >
          {{ shortLabel(label) }}
        </text>

        <path
          v-for="item in drawables"
          :key="item.name"
          :d="item.path"
          class="line"
          :style="{ stroke: item.color }"
        />

        <!-- 悬停：一条竖线 + 每条线上的点 -->
        <template v-if="hoverIndex >= 0">
          <line
            :x1="guideX"
            :x2="guideX"
            :y1="PAD.top"
            :y2="PAD.top + plotHeight"
            class="guide"
          />
          <circle
            v-for="item in drawables"
            :key="item.name"
            :cx="item.points[hoverIndex]?.x ?? 0"
            :cy="item.points[hoverIndex]?.y ?? 0"
            r="3.5"
            :style="{ fill: item.color }"
          />
        </template>

        <!-- 透明覆盖层专收鼠标事件：鼠标划过空白处也仍然算「在图里」，
             否则提示框会在点与点之间反复闪烁 -->
        <rect
          :x="PAD.left"
          :y="PAD.top"
          :width="plotWidth"
          :height="plotHeight"
          fill="transparent"
          @mousemove="onMove"
          @mouseleave="hoverIndex = -1"
        />
      </svg>

      <div v-if="tooltip" class="tip" :style="tooltipStyle">
        <div class="tip-day">{{ tooltip.label }}</div>
        <div v-for="row in tooltip.rows" :key="row.name" class="tip-row">
          <i class="dot" :style="{ background: row.color }" />
          <span class="muted">{{ row.name }}</span>
          <span class="mono tip-value">{{ row.value.toLocaleString() }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 外层只管「图例在上、图表在下」这一列，高度由调用方给（见 UsageView 的 .chart-panel）
 * —— 图才会跟着窗口变高变矮 */
.chart {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  width: 100%;
}

/* 图表区：图例之外的高度全归它。宽高都从这里量（见 script 的 ResizeObserver），
 * position: relative 是给悬停提示框当参照的 */
.plot {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.legend {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-bottom: 4px;
  font-size: var(--fs-xs);
  color: var(--text-soft);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.grid {
  stroke: var(--border);
  stroke-width: 1;
}

.axis {
  fill: var(--text-faint);
  font-size: var(--fs-2xs);
}

.line {
  fill: none;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.guide {
  stroke: var(--text-faint);
  stroke-width: 1;
  stroke-dasharray: 3 3;
}

/* 提示框：pointer-events: none 是关键 —— 它压在图上，
 * 否则鼠标一移过去就会被它挡住，触发覆盖层的 mouseleave */
.tip {
  position: absolute;
  top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  box-shadow: var(--shadow);
  font-size: var(--fs-xs);
  white-space: nowrap;
  pointer-events: none;
}

.tip-day {
  margin-bottom: 4px;
  color: var(--text-soft);
}

.tip-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tip-value {
  margin-left: auto;
  padding-left: 12px;
  font-weight: 500;
}
</style>
