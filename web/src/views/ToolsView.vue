<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { ToolSpec } from '../api/types'

const tools = ref<ToolSpec[]>([])
const categories = ref<string[]>([])
const keyword = ref('')
const category = ref('')

// 筛选放前端做：工具总量本来就很小，来回请求后端反而更慢，输入时也零延迟
const visible = computed(() =>
  tools.value.filter((tool) => {
    if (keyword.value && !tool.name.toLowerCase().includes(keyword.value.toLowerCase())) return false
    if (category.value && tool.category !== category.value) return false
    return true
  }),
)

async function load(): Promise<void> {
  const data = await api.get<{ tools: ToolSpec[]; categories: string[] }>('/tools')
  tools.value = data.tools
  categories.value = data.categories
}

/**
 * 开关一个工具。
 *
 * 参数按字段拆开而不是收整个对象：`el-table` 插槽里的 `row` 类型是宽泛的
 * `DefaultRow`，直接传给要求 `ToolSpec` 的函数过不了类型检查。传字段反而更清楚 ——
 * 这里本来也只用到 name 和 enabled。
 */
async function toggle(name: string, enabled: boolean): Promise<void> {
  // 开关的值已经被 v-model 改好了，这里只负责落盘。
  // 停用的工具不会发给模型，模型自然也就调用不到它
  await api.patch(`/tools/${name}`, { enabled })
  ElMessage.success(`${name} 已${enabled ? '启用' : '停用'}`)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>工具</h1>
      <span class="hint">启用的工具会随每轮对话一起发给模型，由它按需调用；停用的不会出现在菜单里</span>
    </Teleport>

    <div class="page-head">
      <div class="filters">
        <el-input
          v-model="keyword"
          size="small"
          placeholder="按名称过滤"
          clearable
          class="filter"
        />
        <el-select v-model="category" size="small" placeholder="全部分类" clearable class="filter">
          <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
        </el-select>
      </div>
    </div>

    <el-table :data="visible" size="small" stripe>
      <el-table-column label="名称" width="150">
        <template #default="{ row }">
          <span class="mono">{{ row.name }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="category" label="分类" width="90" />
      <el-table-column prop="description" label="功能简介" />

      <el-table-column label="启用" width="80" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row.name, row.enabled)" />
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
/* 靠左而不是靠右：标题搬去顶栏之后，这一行左边没有东西了，
 * margin-left: auto 会把控件推到最右侧、留下一大片空白 */
.filters {
  display: flex;
  gap: 8px;
}

.filter {
  width: 170px;
}
</style>
