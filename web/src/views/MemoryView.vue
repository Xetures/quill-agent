<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { MemoryItem } from '../api/types'
import { useTableHeight } from '../composables/useTableHeight'
import { errorText } from '../utils/error'
import { formatIsoTime } from '../utils/format'

const { t } = useI18n()

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
    ElMessage.success(t('memory.added'))
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
  ElMessage.success(t('common.deleted'))
}

async function clearAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(t('memory.clearConfirm', { count: items.value.length }), t('memory.clearTitle'), {
      type: 'warning',
      confirmButtonText: t('memory.clearAll'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户取消
  }

  await api.del('/memory')
  await load()
  ElMessage.success(t('memory.cleared'))
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>{{ t('memory.title') }}</h1>
      <span class="hint">
        {{ t('memory.hint', { enabled: enabledCount, max }) }}
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
        :placeholder="t('memory.addPlaceholder')"
      />
      <!-- 用默认尺寸（不是 small）：输入框是三行高，按钮再小一档就显得像两个附属品 -->
      <el-button type="primary" :disabled="!draft.trim()" @click="add">{{ t('memory.add') }}</el-button>
      <el-button :disabled="!items.length" @click="clearAll">{{ t('memory.clearAll') }}</el-button>
    </div>

    <!-- 表格自己滚：表头固定、只有表体在滚（高度由 useTableHeight 量出） -->
    <div ref="tableBox" class="table-box">
      <el-table :data="items" :height="tableHeight" size="small" stripe>
      <el-table-column :label="t('memory.columnText')" prop="text" />
      <el-table-column :label="t('memory.columnTime')" width="120">
        <template #default="{ row }">
          <span class="muted">{{ formatIsoTime(row.created_at) }}</span>
        </template>
      </el-table-column>

      <el-table-column :label="t('memory.columnEnabled')" width="80" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row.id, row.enabled)" />
        </template>
      </el-table-column>

      <el-table-column :label="t('memory.columnActions')" width="80" align="center">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="remove(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
      </el-table>
    </div>

    <el-empty
      v-if="!items.length"
      :description="t('memory.empty')"
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
