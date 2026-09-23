<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { ConversationBrief } from '../api/types'
import { useTableHeight } from '../composables/useTableHeight'
import { formatTime } from '../utils/format'

const { t } = useI18n()

const archived = ref<ConversationBrief[]>([])

/** 表格容器：量它的高度交给 el-table，让表体自己滚（见 useTableHeight） */
const tableBox = ref<HTMLElement | null>(null)
const tableHeight = useTableHeight(tableBox)
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
  ElMessage.success(t('archiveList.restored'))
}

async function remove(id: string, title: string): Promise<void> {
  try {
    await ElMessageBox.confirm(t('archiveList.deleteConfirm', { title }), t('archiveList.deleteTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return
  }

  await api.del(`/conversations/${id}`)
  await load()
  ElMessage.success(t('common.deleted'))
}

async function clearAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('archiveList.clearConfirm', { count: archived.value.length }),
      t('archiveList.clearTitle'),
      {
      type: 'warning',
      confirmButtonText: t('memo.clear'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return
  }

  await api.del('/conversations')
  await load()
  ElMessage.success(t('archiveList.cleared'))
}

// 时间格式化见 utils/format —— 这里传 withYear，列表页带上年份更明确

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <!-- 顶栏标题与说明由「偏好设置」统一给（见 SettingsLayout）：这一页是它下面的一节，
         不再自己 Teleport 一个「归档」上去 -->

    <div class="page-head">
      <div class="filters">
        <el-input v-model="keyword" size="small" :placeholder="t('archiveList.searchPlaceholder')" clearable class="search" />
        <!-- 原先挂在顶栏的那句说明，跟着这一节落到页面里 -->
        <span class="muted note">{{ t('archiveList.timeNote') }}</span>
        <el-button size="small" :disabled="!archived.length" @click="clearAll">{{ t('archiveList.clearAll') }}</el-button>
      </div>
    </div>

    <!-- 表格自己滚：表头固定、只有表体在滚（高度由 useTableHeight 量出） -->
    <div ref="tableBox" class="table-box">
      <el-table :data="visible" :height="tableHeight" size="small" stripe>
      <el-table-column :label="t('archiveList.columnTitle')" prop="title" />
      <el-table-column :label="t('archiveList.columnArchivedAt')" width="150">
        <template #default="{ row }">
          <span class="muted">{{ formatTime(row.updated_at, true) }}</span>
        </template>
      </el-table-column>

      <el-table-column :label="t('archiveList.columnActions')" width="150" align="center">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="restore(row.id)">{{ t('archiveList.restore') }}</el-button>
          <el-button size="small" text type="danger" @click="remove(row.id, row.title)">
            {{ t('common.delete') }}
          </el-button>
        </template>
      </el-table-column>
      </el-table>
    </div>

    <el-empty v-if="!visible.length" :description="t('archiveList.empty')" />
  </div>
</template>

<style scoped>
/* 整页不滚，表格自己滚（见 .table-box）—— 和其余列表页一致：
 * 表头与筛选项钉在原处，只有表体在滚 */
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

/* 靠左而不是靠右：标题搬去顶栏之后，这一行左边没有东西了，
 * margin-left: auto 会把控件推到最右侧、留下一大片空白 */
.filters {
  display: flex;
  align-items: center;
  gap: 8px;
}

.note {
  font-size: var(--fs-xs);
}

.search {
  width: 200px;
}
</style>
