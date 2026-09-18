<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { ConversationBrief } from '../api/types'
import { formatTime } from '../utils/format'

const archived = ref<ConversationBrief[]>([])
const keyword = ref('')

const visible = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return archived.value
  return archived.value.filter((item) => item.title.toLowerCase().includes(text))
})

async function load(): Promise<void> {
  const data = await api.get<{ archived: ConversationBrief[] }>('/conversations')
  archived.value = data.archived
}

// 参数按字段拆开：el-table 插槽里的 row 是宽泛的 DefaultRow，
// 直接传整个对象过不了类型检查
async function restore(id: string): Promise<void> {
  await api.post(`/conversations/${id}/restore`)
  await load()
  ElMessage.success('已恢复，回到活跃列表')
}

async function remove(id: string, title: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`永久删除「${title}」？不可撤销。`, '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  await api.del(`/conversations/${id}`)
  await load()
  ElMessage.success('已删除')
}

async function clearAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(`清空全部 ${archived.value.length} 条归档？不可撤销。`, '清空归档', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  await api.del('/conversations')
  await load()
  ElMessage.success('已清空')
}

// 时间格式化见 utils/format —— 这里传 withYear，列表页带上年份更明确

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>归档</h1>
      <span class="hint">
        这里显示的是归档时间。恢复后按原消息时间排序，可能会排到活跃列表靠后。
      </span>
    </Teleport>

    <div class="page-head">
      <div class="filters">
        <el-input v-model="keyword" size="small" placeholder="按标题搜索" clearable class="search" />
        <el-button size="small" :disabled="!archived.length" @click="clearAll">清空归档</el-button>
      </div>
    </div>

    <el-table :data="visible" size="small" stripe>
      <el-table-column prop="title" label="标题" />
      <el-table-column label="归档时间" width="150">
        <template #default="{ row }">
          <span class="muted">{{ formatTime(row.updated_at, true) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="150" align="center">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="restore(row.id)">恢复</el-button>
          <el-button size="small" text type="danger" @click="remove(row.id, row.title)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!visible.length" description="没有归档的会话" />
  </div>
</template>

<style scoped>
/* 靠左而不是靠右：标题搬去顶栏之后，这一行左边没有东西了，
 * margin-left: auto 会把控件推到最右侧、留下一大片空白 */
.filters {
  display: flex;
  gap: 8px;
}

.search {
  width: 200px;
}
</style>
