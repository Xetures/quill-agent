<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import type { Mode, ModeList } from '../api/types'
import { loadOptions, session } from '../stores/session'
import { errorText } from '../utils/error'

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
  { key: '', label: '（不指定）' },
  ...session.models.map((item) => ({ key: item.key, label: item.label })),
])

/** 把「组 id -> 名字」摊成下拉选项；空串那一项表示「这一类不给」。 */
function optionsOf(map: Record<string, string>): { id: string; name: string }[] {
  return Object.entries(map).map(([id, name]) => ({ id, name }))
}

/** 模式表里显示组名；没选（或组已被删）时显示占位。 */
function groupName(kind: 'prompt' | 'tool' | 'skill', id: string): string {
  if (!id) return '—'
  return groups.value[kind][id] ?? '（组已删除）'
}

function modelLabel(key: string): string {
  if (!key) return '不指定'
  return session.models.find((item) => item.key === key)?.label ?? '（模型已删除）'
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
      ElMessage.success('模式已更新')
    } else {
      await api.post('/modes', payload)
      ElMessage.success('模式已创建')
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
    await ElMessageBox.confirm(`删除模式「${name}」？`, '删除模式', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/modes/${id}`)
  await load()
  await loadOptions()
  ElMessage.success('已删除')
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>模式</h1>
      <span class="hint">
        模式是 Agent 的一套完整配置：提示词组 + 工具组 + 技能组 + 记忆开关 + 偏好模型；
        任务页必须选一个
      </span>
    </Teleport>

    <section class="page-body">
      <div class="body-head">
        <el-input
          v-model="keyword"
          size="small"
          placeholder="按模式名或简介过滤"
          clearable
          class="filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">
          添加模式
        </el-button>
      </div>

      <el-scrollbar class="table-wrap">
        <el-table :data="visible" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column prop="name" label="模式名" width="130">
            <template #default="{ row }">
              <span class="mode-name">{{ row.name }}</span>
            </template>
          </el-table-column>

          <el-table-column
            prop="description"
            label="模式简介"
            min-width="150"
            show-overflow-tooltip
          />

          <el-table-column label="提示词组" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.prompt_group_id }">
                {{ groupName('prompt', row.prompt_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="工具组" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.tool_group_id }">
                {{ groupName('tool', row.tool_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="技能组" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.skill_group_id }">
                {{ groupName('skill', row.skill_group_id) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="记忆" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.memory_enabled ? 'success' : 'info'">
                {{ row.memory_enabled ? '启用' : '关闭' }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="偏好模型" width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ muted: !row.preferred_model }">{{ modelLabel(row.preferred_model) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="130" align="center">
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
                编辑
              </el-button>
              <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
                删除
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="还没有模式；点右上角「添加模式」创建" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗。编辑复用同一张表单，只多带一份初值 -->
    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑模式' : '添加模式'" width="520px">
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item label="模式名" required>
          <el-input v-model="form.name" placeholder="例如：标准 Agent" maxlength="30" />
        </el-form-item>

        <el-form-item label="模式简介" required>
          <el-input
            v-model="form.description"
            placeholder="一句话说明这个模式用来做什么"
            maxlength="60"
          />
        </el-form-item>

        <el-divider content-position="left">模式构成</el-divider>

        <!-- 三个组都可以不选：纯问答模式就是什么都不给 -->
        <el-form-item label="提示词组">
          <el-select
            v-model="form.prompt_group_id"
            clearable
            :placeholder="
              optionsOf(groups.prompt).length ? '不使用提示词组' : '（还没有提示词组）'
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

        <el-form-item label="工具组">
          <el-select
            v-model="form.tool_group_id"
            clearable
            :placeholder="optionsOf(groups.tool).length ? '不给任何工具' : '（还没有工具组）'"
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

        <el-form-item label="技能组">
          <el-select
            v-model="form.skill_group_id"
            clearable
            :placeholder="optionsOf(groups.skill).length ? '不给任何技能' : '（还没有技能组）'"
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

        <el-form-item label="启用记忆">
          <el-switch v-model="form.memory_enabled" />
          <span class="field-hint muted">
            {{ form.memory_enabled ? '把「关于用户的已知信息」拼进上下文' : '这个模式不记得用户' }}
          </span>
        </el-form-item>

        <el-form-item label="偏好模型">
          <el-select v-model="form.preferred_model" placeholder="不指定" class="wide">
            <el-option
              v-for="item in modelOptions"
              :key="item.key"
              :label="item.label"
              :value="item.key"
            />
          </el-select>
          <div class="field-hint muted">
            在任务页切到这个模式时会自动换过去；不指定则保持当前模型
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">取消</el-button>
        <el-button size="small" type="primary" :disabled="!canSubmit" @click="submit">
          确认
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
  font-size: 12px;
  line-height: 1.4;
}

.wide {
  width: 100%;
}
</style>
