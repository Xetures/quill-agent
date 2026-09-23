<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { UsageReport } from '../api/types'
import LineChart from '../components/LineChart.vue'
import { errorText } from '../utils/error'
import { formatTime } from '../utils/format'

/**
 * 用量页。
 *
 * 只统计 token，不折算金额 —— 各家怎么计价、有没有缓存折扣、什么时候调价，
 * 是供应商自己的事；token 数才是唯一准确、且各家口径一致的东西。
 */

const report = ref<UsageReport | null>(null)

const series = computed(() =>
  (report.value?.series ?? [])
    // 全是 0 的模型不上图：折线图里一条贴底的直线不带任何信息，只会把图例撑长、
    // 把真正用过的几档颜色挤到边角。它要是想找「配了但没用过」的模型，看下面的表格
    .filter((item) => item.values.some((value) => value > 0))
    .map((item) => ({ name: item.model, values: item.values })),
)

const tasks = computed(() => report.value?.tasks ?? [])

async function load(): Promise<void> {
  try {
    report.value = await api.get<UsageReport>('/usage')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
}

/** 一个任务中途可以换模型，所以这里可能是好几个名字。 */
function modelText(models: string[]): string {
  return models.length ? models.join('、') : '—'
}

/**
 * 表格区的高度，量出来再交给 el-table。
 *
 * 自己套一层 `el-scrollbar` 也能滚，但那样表头会跟着列表一起滚走，滑到下面
 * 就分不清哪列是哪列了。el-table 给了高度才会「表头固定 + 表体内部滚动」，
 * 而它的 height 只认数字或 px 字符串 —— 传 `100%` 它算不出表体高度，
 * 等于没设。所以只能量。
 */
const tableBox = ref<HTMLElement | null>(null)
const tableHeight = ref(320)
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!tableBox.value) return

  observer = new ResizeObserver((entries) => {
    const measured = entries[0]?.contentRect.height ?? 0
    if (measured) tableHeight.value = Math.round(measured)
  })
  observer.observe(tableBox.value)
})

onBeforeUnmount(() => observer?.disconnect())

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>用量</h1>
      <span class="hint">
        token 消耗：折线按模型分色看 7 天，表格按任务汇总
      </span>
    </Teleport>

    <section class="panel chart-panel">
      <h2>每日用量</h2>

      <!-- 一条线都没有时不画空坐标系：那只是个更占地方的空状态 -->
      <LineChart v-if="series.length" :labels="report?.dates ?? []" :series="series" />
      <el-empty v-else description="最近 7 天还没有用量记录" :image-size="60" />
    </section>

    <section class="panel table-panel">
      <h2>按任务</h2>

      <!-- 列表自己滚：和页面共用一条滚动条的话，滑到列表底部统计图就被顶出视口了，
           而这两块本来是要对着看的。高度由 ResizeObserver 量出来交给 el-table，
           这样表头固定、只有表体在滚 -->
      <div ref="tableBox" class="table-box">
        <el-table :data="tasks" :height="tableHeight" size="small" stripe>
          <el-table-column prop="title" label="任务名" min-width="200" show-overflow-tooltip />

          <el-table-column label="模型名" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.models.length }">{{ modelText(row.models) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="创建时间" width="150">
            <template #default="{ row }">
              <span class="mono">{{ formatTime(row.created_at, true) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="token 用量" width="120" align="right">
            <template #default="{ row }">
              <span class="mono">{{ row.tokens.toLocaleString() }}</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="96" align="center">
            <template #default="{ row }">
              <el-tag :type="row.archived ? 'info' : 'success'" size="small" effect="plain">
                {{ row.archived ? '已归档' : '未归档' }}
              </el-tag>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="还没有任何用量记录" :image-size="60" />
          </template>
        </el-table>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 页面本身不滚：图固定在上面，只有下面的表格区滚 —— 两块是一起看的，
 * 滑到列表底部时统计图不该被顶出视口 */
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.panel {
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
}

/* 两块**平分**页面高度，各自随窗口长缩：
 * 图表那边的高度是从容器量出来的（见 LineChart），所以窗口一变高图就变高，
 * 不再是一张写死 240px、面板一矮就被裁的图 */
.chart-panel {
  display: flex;
  flex: 1 1 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

/* 图例最多两行，多出来的在图例条内部滚 —— 模型一多它会折成三四行，
 * 那是唯一会把图挤矮的东西 */
.chart-panel :deep(.legend) {
  flex-shrink: 0;
  max-height: 2.4em;
  overflow-y: auto;
}

/* 表格区同样平分，但它有个下限：低于这个高度就只剩表头了 */
.table-panel {
  display: flex;
  flex: 1 1 0;
  min-height: 220px;
  flex-direction: column;
}

/* 量高度用的容器：吃掉剩余高度，el-table 再按量出来的数字定高 */
.table-box {
  flex: 1;
  min-height: 0;
}

.panel h2 {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
}
</style>
