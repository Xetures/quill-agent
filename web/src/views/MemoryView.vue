<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { MemoryItem } from '../api/types'
import { errorText } from '../utils/error'
import { formatIsoTime } from '../utils/format'

const items = ref<MemoryItem[]>([])
const max = ref(50)
const draft = ref('')

const enabledCount = computed(() => items.value.filter((item) => item.enabled).length)

async function load(): Promise<void> {
  const data = await api.get<{ items: MemoryItem[]; max: number }>('/memory')
  items.value = data.items
  max.value = data.max
}

async function add(): Promise<void> {
  const text = draft.value.trim()
  if (!text) return

  try {
    await api.post('/memory', { text })
    draft.value = ''
    await load()
    ElMessage.success('已添加')
  } catch (exc) {
    // 后端的「重复了 / 太长了 / 已满了」原样弹出来 —— 那些文案本来就是给人看的
    ElMessage.error(errorText(exc))
  }
}

// 参数按字段拆开：el-table 插槽里的 row 是宽泛的 DefaultRow，
// 直接传给要求 MemoryItem 的函数过不了类型检查
async function toggle(id: string, enabled: boolean): Promise<void> {
  await api.patch(`/memory/${id}`, { enabled })
}

async function remove(id: string): Promise<void> {
  await api.del(`/memory/${id}`)
  await load()
  ElMessage.success('已删除')
}

async function clearAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(`清空全部 ${items.value.length} 条记忆？此操作不可撤销。`, '确认', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }

  await api.del('/memory')
  await load()
  ElMessage.success('已清空')
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>记忆</h1>
      <span class="hint">
        跨会话保留的长期事实，启用中的会以清单形式进入每一轮对话 —— 当前 {{ enabledCount }} / {{ max }} 条启用
      </span>
    </Teleport>

    <div class="composer">
      <el-input
        v-model="draft"
        size="small"
        placeholder="手动加一条，例如：偏好简洁回答，不要啰嗦的总结"
        @keydown.enter="add"
      />
      <el-button size="small" type="primary" :disabled="!draft.trim()" @click="add">
        添加
      </el-button>
      <el-button size="small" :disabled="!items.length" @click="clearAll">清空</el-button>
    </div>

    <el-table :data="items" size="small" stripe>
      <el-table-column prop="text" label="内容" />
      <el-table-column label="记录时间" width="120">
        <template #default="{ row }">
          <span class="muted">{{ formatIsoTime(row.created_at) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="启用" width="80" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row.id, row.enabled)" />
        </template>
      </el-table-column>

      <el-table-column label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="remove(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!items.length"
      description="还没有记忆。可以让模型在对话里「记住这件事」，也可以在上面手动加"
    />
  </div>
</template>

<style scoped>
.composer {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
