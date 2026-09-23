<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { MemoryItem } from '../api/types'
import { useTableHeight } from '../composables/useTableHeight'
import { errorText } from '../utils/error'
import { formatIsoTime } from '../utils/format'

const items = ref<MemoryItem[]>([])

/** 表格容器：量它的高度交给 el-table，让表体自己滚（见 useTableHeight） */
const tableBox = ref<HTMLElement | null>(null)
const tableHeight = useTableHeight(tableBox)
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
        跨会话保留的长期事实，启用中的每轮都会带上 —— 当前 {{ enabledCount }} / {{ max }} 条
      </span>
    </Teleport>

    <div class="composer">
      <!-- 三行高：记忆是一条完整的陈述句，单行输入框改起来要来回左右看。
           既然是多行，Enter 就留给换行，提交走按钮 -->
      <el-input
        v-model="draft"
        type="textarea"
        :rows="3"
        resize="none"
        placeholder="手动加一条，例如：偏好简洁回答，不要啰嗦的总结"
      />
      <!-- 用默认尺寸（不是 small）：输入框是三行高，按钮再小一档就显得像两个附属品 -->
      <el-button type="primary" :disabled="!draft.trim()" @click="add">添加</el-button>
      <el-button :disabled="!items.length" @click="clearAll">清空</el-button>
    </div>

    <!-- 表格自己滚：表头固定、只有表体在滚（高度由 useTableHeight 量出） -->
    <div ref="tableBox" class="table-box">
      <el-table :data="items" :height="tableHeight" size="small" stripe>
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
    </div>

    <el-empty
      v-if="!items.length"
      description="还没有记忆。可以让模型在对话里「记住这件事」，也可以在上面手动加"
    />
  </div>
</template>

<style scoped>
/* 整页不滚，表格自己滚（见 .table-box）—— 表头与输入行钉在原处，只有表体在滚 */
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.table-box {
  flex: 1;
  min-height: 0;
}

.composer {
  display: flex;
  /* 垂直居中：输入框是三行高，按钮贴顶会显得它比实际更小、位置也偏上 */
  align-items: center;
  gap: 8px;
  /* 和下面列表之间的距离由 .page 的 gap 给，这里不再另加 —— 两个一起会叠成两倍 */
}
</style>
