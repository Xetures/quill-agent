<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { Mode, ModeList } from '../api/types'
import { loadOptions, session } from '../stores/session'
import { errorText } from '../utils/error'

const { t } = useI18n()

// ---------------------------------------------------------------------------
// 数据
//
// 模式 = 提示词组 + 工具组 + 技能组 + 记忆开关 + 偏好模型。
// 它引用三类组，本身不保存成员 —— 同一个组可以被多个模式复用。
// ---------------------------------------------------------------------------

const modes = ref<Mode[]>([])
/** 三类组 id -> 名字；随 /modes 一起返回，省得再拉三份列表。 */
const groups = ref<ModeList['groups']>({ prompt: {}, tool: {}, skill: {} })

const keyword = ref('')
const dialogOpen = ref(false)
/** 正在编辑的模式 id；空串表示新建。 */
const editingId = ref('')

const form = reactive({
  name: '',
  description: '',
  prompt_group_id: '',
  tool_group_id: '',
  skill_group_id: '',
  memory_enabled: true,
  preferred_model: '',
})

const visible = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return modes.value
  return modes.value.filter(
    (mode) =>
      mode.name.toLowerCase().includes(text) || mode.description.toLowerCase().includes(text),
  )
})

/** 偏好模型下拉：空串表示「不指定」。 */
const modelOptions = computed(() => [
  { key: '', label: t('modes.form.modelAny') },
  ...session.models.map((item) => ({ key: item.key, label: item.label })),
])

/** 把「组 id -> 名字」摊成下拉选项；空串那一项表示「这一类不给」。 */
function optionsOf(map: Record<string, string>): { id: string; name: string }[] {
  return Object.entries(map).map(([id, name]) => ({ id, name }))
}

/** 模式表里显示组名；没选（或组已被删）时显示占位。 */
function groupName(kind: 'prompt' | 'tool' | 'skill', id: string): string {
  if (!id) return '—'
  return groups.value[kind][id] ?? t('modes.groupDeleted')
}

function modelLabel(key: string): string {
  if (!key) return t('modes.form.modelUnset')
  return session.models.find((item) => item.key === key)?.label ?? t('modes.modelDeleted')
}

async function load(): Promise<void> {
  const data = await api.get<ModeList>('/modes')
  modes.value = data.modes
  groups.value = data.groups
}

function openCreate(): void {
  editingId.value = ''
  form.name = ''
  form.description = ''
  form.prompt_group_id = ''
  form.tool_group_id = ''
  form.skill_group_id = ''
  // 记忆默认开：它是「记得住用户」那一层，关掉才需要用户明确表达
  form.memory_enabled = true
  form.preferred_model = ''
  dialogOpen.value = true
}

// 参数按字段拆开：`el-table` 插槽里的 `row` 类型是宽泛的 DefaultRow，
// 直接传整个对象过不了类型检查
function openEdit(
  id: string,
  name: string,
  description: string,
  promptGroupId: string,
  toolGroupId: string,
  skillGroupId: string,
  memoryEnabled: boolean,
  preferredModel: string,
): void {
  editingId.value = id
  form.name = name
  form.description = description
  form.prompt_group_id = promptGroupId
  form.tool_group_id = toolGroupId
  form.skill_group_id = skillGroupId
  form.memory_enabled = memoryEnabled
  form.preferred_model = preferredModel
  dialogOpen.value = true
}

const canSubmit = computed(() => Boolean(form.name.trim() && form.description.trim()))

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  const payload = {
    name: form.name.trim(),
    description: form.description.trim(),
    prompt_group_id: form.prompt_group_id,
    tool_group_id: form.tool_group_id,
    skill_group_id: form.skill_group_id,
    memory_enabled: form.memory_enabled,
    preferred_model: form.preferred_model,
  }

  try {
    if (editingId.value) {
      await api.put(`/modes/${editingId.value}`, payload)
      ElMessage.success(t('modes.updated'))
    } else {
      await api.post('/modes', payload)
      ElMessage.success(t('modes.created'))
    }
    dialogOpen.value = false
    await load()
    // 任务页的模式选择器读的是同一份数据，改完得让它看到最新的，
    // 否则选中项会指向刚被删掉的模式
    await loadOptions()
  } catch (exc) {
    // 模式名重复：后端的文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  }
}

async function remove(id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(t('modes.deleteConfirm', { name }), t('modes.deleteTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/modes/${id}`)
  await load()
  await loadOptions()
  ElMessage.success(t('common.deleted'))
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>{{ t('modes.title') }}</h1>
      <span class="hint">
        {{ t('modes.hint') }}
      </span>
    </Teleport>

    <section class="page-body">
      <div class="body-head">
        <el-input
          v-model="keyword"
          size="small"
          :placeholder="t('modes.filter')"
          clearable
          class="filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">
          {{ t('modes.add') }}
        </el-button>
      </div>

      <el-scrollbar class="table-wrap">
        <el-table :data="visible" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column :label="t('modes.columnName')" prop="name" width="180">
            <template #default="{ row }">
              <span class="mode-name">{{ row.name }}</span>
              <!-- 出厂资源标出来。下面的删除按钮对它是禁用的，不说明的话用户会以为
                   界面坏了（硬约束在存储层，见 quill_agent/defaults.py） -->
              <el-tag v-if="row.builtin" size="small" effect="plain" class="builtin">{{ t('modes.builtin') }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column
            prop="description"
            :label="t('modes.columnDesc')"
            min-width="150"
            show-overflow-tooltip
          />

          <el-table-column :label="t('modes.columnPromptGroup')" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.prompt_group_id }">
                {{ groupName('prompt', row.prompt_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column :label="t('modes.columnToolGroup')" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.tool_group_id }">
                {{ groupName('tool', row.tool_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column :label="t('modes.columnSkillGroup')" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.skill_group_id }">
                {{ groupName('skill', row.skill_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column :label="t('modes.columnMemory')" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.memory_enabled ? 'success' : 'info'">
                {{ row.memory_enabled ? t('modes.memoryOn') : t('modes.memoryOff') }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="t('modes.columnModel')" width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.preferred_model }">{{ modelLabel(row.preferred_model) }}</span>
            </template>
          </el-table-column>

          <el-table-column :label="t('modes.columnActions')" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="
                  openEdit(
                    row.id,
                    row.name,
                    row.description,
                    row.prompt_group_id,
                    row.tool_group_id,
                    row.skill_group_id,
                    row.memory_enabled,
                    row.preferred_model,
                  )
                "
              >
                {{ t('common.edit') }}
              </el-button>
              <!-- 内置模式不给删除入口（编辑照旧留着）。真正的拒绝在存储层 ——
                   这里少了这个判断，用户点了会拿到一句 400，那是失败的体验而不是保护 -->
              <el-button
                v-if="!row.builtin"
                size="small"
                text
                type="danger"
                @click="remove(row.id, row.name)"
              >
                {{ t('common.delete') }}
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty :description="t('modes.empty')" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗。编辑复用同一张表单，只多带一份初值 -->
    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId ? t('modes.form.editTitle') : t('modes.form.addTitle')"
      width="520px"
      append-to-body
    >
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item :label="t('modes.form.name')" required>
          <el-input v-model="form.name" :placeholder="t('modes.form.namePlaceholder')" maxlength="30" />
        </el-form-item>

        <el-form-item :label="t('modes.form.desc')" required>
          <el-input
            v-model="form.description"
            :placeholder="t('modes.form.descPlaceholder')"
            maxlength="60"
          />
        </el-form-item>

        <el-divider content-position="left">{{ t('modes.form.composition') }}</el-divider>

        <!-- 三个组都可以不选：纯问答模式就是什么都不给 -->
        <el-form-item :label="t('modes.form.promptGroup')">
          <el-select
            v-model="form.prompt_group_id"
            clearable
            :placeholder="
              optionsOf(groups.prompt).length ? t('modes.form.promptNone') : t('modes.form.promptEmpty')
            "
            class="wide"
          >
            <el-option
              v-for="item in optionsOf(groups.prompt)"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('modes.form.toolGroup')">
          <el-select
            v-model="form.tool_group_id"
            clearable
            :placeholder="optionsOf(groups.tool).length ? t('modes.form.toolNone') : t('modes.form.toolEmpty')"
            class="wide"
          >
            <el-option
              v-for="item in optionsOf(groups.tool)"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('modes.form.skillGroup')">
          <el-select
            v-model="form.skill_group_id"
            clearable
            :placeholder="optionsOf(groups.skill).length ? t('modes.form.skillNone') : t('modes.form.skillEmpty')"
            class="wide"
          >
            <el-option
              v-for="item in optionsOf(groups.skill)"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('modes.form.memory')">
          <el-switch v-model="form.memory_enabled" />
          <span class="field-hint muted">
            {{ form.memory_enabled ? t('modes.form.memoryOnHint') : t('modes.form.memoryOffHint') }}
          </span>
        </el-form-item>

        <el-form-item :label="t('modes.form.preferredModel')">
          <el-select v-model="form.preferred_model" :placeholder="t('modes.form.modelUnset')" class="wide">
            <el-option
              v-for="item in modelOptions"
              :key="item.key"
              :label="item.label"
              :value="item.key"
            />
          </el-select>
          <div class="field-hint muted">
            {{ t('modes.form.modelHint') }}
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button size="small" type="primary" :disabled="!canSubmit" @click="submit">
          {{ t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

.body-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

/* 搜索框吃掉剩余宽度，按钮贴右 */
.filter {
  flex: 1;
  max-width: 260px;
  margin-right: auto;
}

.table-wrap {
  flex: 1;
  min-height: 0;
}

.mode-name {
  font-weight: 500;
}

.field-hint {
  margin-left: 10px;
  font-size: var(--fs-xs);
  line-height: 1.4;
}

.wide {
  width: 100%;
}
</style>
