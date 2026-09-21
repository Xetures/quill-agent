<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api/client'

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
  <div class="body">
    <div class="block">
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
    </div>

    <div class="block">
      <p class="muted desc">
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
    </div>
  </div>
</template>

<style scoped>
/* 长句子不铺满整行才读得进去 */
.body {
  max-width: 640px;
}

/* 两段说明各自成块，中间用留白分开 —— 原先靠一条分隔线，现在一节一页，
   分隔线没了反而更清爽 */
.block + .block {
  margin-top: 26px;
}

.desc {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
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
</style>
