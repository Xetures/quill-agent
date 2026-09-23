<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { ToolGroup, ToolSpec } from '../api/types'
import { errorText } from '../utils/error'
import { useToolCategory } from '../utils/tool-category'

const { t } = useI18n()

// ---------------------------------------------------------------------------
// 数据
// ---------------------------------------------------------------------------

/** 工具组里引用「整个 MCP 服务器」的前缀。和后端 `agent.MCP_REF_PREFIX` 是同一口径。 */
const MCP_REF = 'mcp:'

/** MCP 服务器在工具组界面里只需要这几个字段。 */
interface McpServerBrief {
  id: string
  name: string
  connected: boolean
  tools: string[]
}

const mcpServers = ref<McpServerBrief[]>([])

const tools = ref<ToolSpec[]>([])
const categories = ref<string[]>([])
const groups = ref<ToolGroup[]>([])

const keyword = ref('')
const category = ref('')

// 分类的显示名与顺序都归它管（后端给的是 `files` 这种 key，不是文案）
const { label: categoryLabel, sort: sortCategories } = useToolCategory()
const groupKeyword = ref('')

// 工具筛选放前端做：工具总量本来就很小，来回请求后端反而更慢，输入时也零延迟
const visible = computed(() =>
  tools.value.filter((tool) => {
    if (keyword.value && !tool.name.toLowerCase().includes(keyword.value.toLowerCase())) return false
    if (category.value && tool.category !== category.value) return false
    return true
  }),
)

// 组筛选同样放前端，理由同上；顺带匹配简介，组的数量也不会多
const visibleGroups = computed(() => {
  const text = groupKeyword.value.trim().toLowerCase()
  if (!text) return groups.value
  return groups.value.filter(
    (group) =>
      group.name.toLowerCase().includes(text) || group.description.toLowerCase().includes(text),
  )
})

async function load(): Promise<void> {
  const [toolData, groupData, mcpData] = await Promise.all([
    api.get<{ tools: ToolSpec[]; categories: string[] }>('/tools'),
    api.get<{ groups: ToolGroup[] }>('/tool-groups'),
    // MCP 服务器：工具组里可以「引入整个服务器」，所以这里也要拉一份
    api.get<{ servers: McpServerBrief[] }>('/mcp/servers'),
  ])
  tools.value = toolData.tools
  // 顺序由前端定（按重要性，而不是后端那个字母序 —— 那是 key 的字母序，对界面没意义）
  categories.value = sortCategories(toolData.categories)
  groups.value = groupData.groups
  mcpServers.value = mcpData.servers
}

/**
 * 表单里引用了哪些 MCP 服务器。
 *
 * 值形如 `mcp:<server_id>`，和真工具名放在同一个列表里 —— 后端在 `resolve_mode` 里
 * 展开它（见 `agent._expand_mcp_refs`）。这样工具组的数据结构不用为新概念改形状。
 */
const selectedServers = computed(() =>
  form.tools.filter((name) => name.startsWith(MCP_REF)).map((name) => name.slice(MCP_REF.length)),
)

function toggleServer(serverId: string, checked: boolean): void {
  const ref = `${MCP_REF}${serverId}`
  form.tools = checked
    ? [...form.tools.filter((name) => name !== ref), ref]
    : form.tools.filter((name) => name !== ref)
}

// ---------------------------------------------------------------------------
// 工具组：新建 / 编辑共用一个弹窗
//
// 编辑就是「带着初值打开新建弹窗」—— 两张表单字段完全一致，拆成两个弹窗
// 只会让改字段时漏掉一边。
// ---------------------------------------------------------------------------

const dialogOpen = ref(false)
/** 正在编辑的组 id；空串表示新建。 */
const editingId = ref('')
const form = reactive({
  name: '',
  description: '',
  tools: [] as string[],
  /** `tools` 里每次调用都要用户点头的那些。是它的子集，见下面那个 watch。 */
  confirm: [] as string[],
})

function openCreate(): void {
  editingId.value = ''
  form.name = ''
  form.description = ''
  form.tools = []
  form.confirm = []
  dialogOpen.value = true
}

function openEdit(
  id: string,
  name: string,
  description: string,
  toolNames: string[],
  confirmNames: string[],
): void {
  editingId.value = id
  form.name = name
  form.description = description
  // 拷贝一份再改：直接绑原数组的话，取消编辑也会改动列表里的数据
  form.tools = [...toolNames]
  form.confirm = [...(confirmNames ?? [])]
  dialogOpen.value = true
}

const canSubmit = computed(() => Boolean(form.name.trim() && form.description.trim()))

/**
 * 可以要求确认的候选：只有组内已有的工具。
 *
 * 「不在场却要求确认」是自相矛盾的配置（那个工具压根不会发给模型），后端会直接
 * 400。与其让用户撞一次报错，不如这里就不给选。
 */
const confirmableTools = computed(() => tools.value.filter((tool) => form.tools.includes(tool.name)))

// 工具被移出组时，连带把它从「需要确认」里摘掉 —— 否则提交必然 400
watch(
  () => [...form.tools],
  () => {
    form.confirm = form.confirm.filter((name) => form.tools.includes(name))
  },
  { deep: true },
)

/** 全选：一次把所有工具放进组里。工具总数不多，比逐个点省事。 */
function selectAllTools(): void {
  form.tools = tools.value.map((tool) => tool.name)
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  const payload = {
    name: form.name.trim(),
    description: form.description.trim(),
    tools: form.tools,
    confirm: form.confirm,
  }

  try {
    if (editingId.value) {
      await api.put(`/tool-groups/${editingId.value}`, payload)
      ElMessage.success(t('tools.groupUpdated'))
    } else {
      await api.post('/tool-groups', payload)
      ElMessage.success(t('tools.groupCreated'))
    }
    dialogOpen.value = false
    await load()
  } catch (exc) {
    // 重名、未知工具名：后端的文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  }
}

// 参数按字段拆开：el-table 插槽里的 row 是宽泛的 DefaultRow，
// 直接传整个对象过不了类型检查（openEdit 同理）
async function remove(id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(t('tools.deleteConfirm', { name }), t('tools.deleteTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/tool-groups/${id}`)
  await load()
  ElMessage.success(t('common.deleted'))
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>{{ t('tools.title') }}</h1>
      <span class="hint">
\1{{ t('tools.hint') }}
      </span>
    </Teleport>

    <!-- 上半：工具组 -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('tools.groupTitle') }}</h2>
        <el-input
          v-model="groupKeyword"
          size="small"
          :placeholder="t('tools.groupFilter')"
          clearable
          class="group-filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">{{ t('tools.groupCreate') }}</el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visibleGroups" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column prop="name" :label="t('tools.columnGroupName')" width="180">
            <template #default="{ row }">
              <span class="group-name">{{ row.name }}</span>
              <el-tag v-if="row.builtin" size="small" effect="plain" class="builtin">{{ t('tools.builtin') }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="t('tools.columnDesc')" prop="description" min-width="160" show-overflow-tooltip />

          <el-table-column :label="t('tools.columnGroupTools')" min-width="220">
            <template #default="{ row }">
              <!-- 空列表是合法状态：「纯对话」组要的就是一个工具都不给。
                   必须显式说出来，否则看起来像漏填了 -->
              <span v-if="!row.tools.length" class="muted">{{ t('tools.groupEmptyTools') }}</span>
              <template v-else>
                <el-tag v-for="name in row.tools" :key="name" size="small" class="tool-tag">
                  {{ name }}
                </el-tag>
              </template>
            </template>
          </el-table-column>

          <el-table-column :label="t('tools.columnActions')" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="openEdit(row.id, row.name, row.description, row.tools, row.confirm)"
              >
\1{{ t('common.edit') }}
              </el-button>
              <!-- 内置项不给删除入口，编辑照旧留着（硬约束在存储层，见
                   quill_agent/defaults.py）。它装着注册表里的全部工具，
                   删掉「开箱即用」就没了 -->
              <el-button
                v-if="!row.builtin"
                size="small"
                text
                type="danger"
                @click="remove(row.id, row.name)"
              >
\1{{ t('common.delete') }}
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty :description="t('tools.groupEmpty')" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 下半：单个工具的开关（原有功能） -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('tools.allTitle') }}</h2>
        <el-input v-model="keyword" size="small" :placeholder="t('tools.filterByName')" clearable class="filter" />
        <!-- 选项的值是**分类 key**（`files` 这种），标签才是翻译过的显示名 ——
             筛选用值、显示用标签，两件事不能混 -->
        <el-select v-model="category" size="small" :placeholder="t('tools.allCategories')" clearable class="filter">
          <el-option
            v-for="item in categories"
            :key="item"
            :label="categoryLabel(item)"
            :value="item"
          />
        </el-select>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visible" size="small" stripe>
          <el-table-column :label="t('tools.columnName')" width="150">
            <template #default="{ row }">
              <span class="mono">{{ row.name }}</span>
            </template>
          </el-table-column>

          <el-table-column :label="t('tools.columnCategory')" width="90">
            <template #default="{ row }">
              <span class="muted">{{ categoryLabel(row.category) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('tools.columnDesc')" prop="description" />

          <!-- 这里不再有「启用」开关：给不给工具由模式的工具组决定。
               全局开关留着就会出现「组里选了却用不了」，而界面上还查不出原因 -->
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗。编辑复用同一张表单，只多带一份初值 -->
    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId ? t('tools.groupForm.editTitle') : t('tools.groupForm.createTitle')"
      width="480px"
      append-to-body
    >
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item :label="t('tools.groupForm.name')" required>
          <el-input v-model="form.name" :placeholder="t('tools.groupForm.namePlaceholder')" maxlength="30" />
        </el-form-item>

        <el-form-item :label="t('tools.groupForm.desc')" required>
          <el-input
            v-model="form.description"
            :placeholder="t('tools.groupForm.descPlaceholder')"
            maxlength="60"
          />
        </el-form-item>

        <!-- 「引入整个服务器」放在逐个勾选**前面**：它是更粗的粒度，先决定要不要这批能力 -->
        <el-form-item :label="t('tools.groupForm.mcp')">
          <div v-if="!mcpServers.length" class="hint">
            {{ t('tools.groupForm.mcpEmpty') }}
          </div>
          <div v-else class="mcp-refs">
            <el-checkbox
              v-for="server in mcpServers"
              :key="server.id"
              :model-value="selectedServers.includes(server.id)"
              @update:model-value="(value: boolean | string | number) => toggleServer(server.id, Boolean(value))"
            >
              {{ server.name }}
              <span class="muted">
                （{{ server.connected ? t('tools.groupForm.mcpTools', { count: server.tools.length }) : t('tools.groupForm.mcpNotConnected') }}）
              </span>
            </el-checkbox>
          </div>
          <div class="hint">
            <span v-html="t('tools.groupForm.mcpHint')" />
          </div>
        </el-form-item>

        <el-form-item :label="t('tools.groupForm.tools')">
          <div class="select-block">
            <div class="select-bar">
              <span class="muted count">{{ t('tools.groupForm.count', { selected: form.tools.length, total: tools.length }) }}</span>
              <el-button link size="small" type="primary" @click="selectAllTools">{{ t('tools.groupForm.selectAll') }}</el-button>
              <el-button link size="small" @click="form.tools = []">{{ t('tools.groupForm.clear') }}</el-button>
            </div>

            <!-- multiple + collapse-tags：工具最多十几个，但每条较长，收起来才能
                 一眼看到选了哪几个。
                 选项里带上分类和简介 —— 只看工具名不容易判断它是干嘛的 -->
            <el-select
              v-model="form.tools"
              multiple
              collapse-tags
              collapse-tags-tooltip
              popper-class="tool-opt-popper"
              :placeholder="t('tools.groupForm.selectPlaceholder')"
              class="tool-select"
            >
              <el-option v-for="tool in tools" :key="tool.name" :label="tool.name" :value="tool.name">
                <span class="opt-name mono">{{ tool.name }}</span>
                <el-tag size="small" effect="plain">{{ categoryLabel(tool.category) }}</el-tag>
                <span class="opt-desc muted">{{ tool.description }}</span>
              </el-option>
            </el-select>
          </div>
        </el-form-item>

        <!-- 「给，但动手前问我」。这是把 run_command 这类工具变得敢用的关键一档：
             没有它，用户只能在「把一台机器交给模型」和「这工具一点用没有」之间选 -->
        <el-form-item :label="t('tools.groupForm.confirm')">
          <div class="select-block">
            <span class="muted confirm-hint">
              {{ t('tools.groupForm.confirmHint') }}
            </span>

            <el-select
              v-model="form.confirm"
              multiple
              collapse-tags
              collapse-tags-tooltip
              popper-class="tool-opt-popper"
              :disabled="!form.tools.length"
              :placeholder="
                form.tools.length ? t('tools.groupForm.confirmSome') : t('tools.groupForm.confirmNone')
              "
              class="tool-select"
            >
              <el-option
                v-for="tool in confirmableTools"
                :key="tool.name"
                :label="tool.name"
                :value="tool.name"
              >
                <span class="opt-name mono">{{ tool.name }}</span>
                <el-tag size="small" effect="plain">{{ categoryLabel(tool.category) }}</el-tag>
                <span class="opt-desc muted">{{ tool.description }}</span>
              </el-option>
            </el-select>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button size="small" type="primary" :disabled="!canSubmit" @click="submit">
          {{ editingId ? t('common.save') : t('common.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 上下两半平均分：页面高度固定（.page 撑满主区），两个 half 各占一半，
 * 各自的表格在内部滚动 —— 上下不会互相挤占 */
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.half {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

.half-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.half-head h2 {
  margin: 0;
  font-size: var(--fs-base);
  font-weight: 600;
}

/* 搜索框吃掉剩余宽度，按钮贴右 —— 一行放得下也不显挤 */
.group-filter {
  flex: 1;
  max-width: 260px;
  margin-left: auto;
}

.filter {
  width: 170px;
}

/* 表格区在 half 里滚动，而不是让整个页面滚 */
.half-body {
  flex: 1;
  min-height: 0;
}

.group-name {
  font-weight: 500;
}

.tool-tag {
  margin: 0 4px 2px 0;
}

.tool-select {
  width: 100%;
}

.confirm-hint {
  display: block;
  margin-bottom: 6px;
  font-size: var(--fs-xs);
}

/* MCP 服务器引用：竖排复选框，每个一行 */
.mcp-refs {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hint {
  margin: 4px 0 0;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-soft);
}

/* 工具列表：一行「已选 x/y」+ 全选 / 清空，再下面是多选下拉 */
.select-block {
  width: 100%;
}

.select-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-bottom: 4px;
}

.select-bar .count {
  margin-right: auto;
  font-size: var(--fs-xs);
}
</style>
