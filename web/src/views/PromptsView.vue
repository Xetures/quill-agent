<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import type { PromptGroup, PromptLib } from '../api/types'
import { loadOptions } from '../stores/session'
import { errorText } from '../utils/error'

// ---------------------------------------------------------------------------
// 数据
//
// 一个提示词组 = 从六类提示词里各挑一个（可以都不挑）。模式现在指三类组 + 记忆的
// 组合，这一页只管提示词那一类 —— 偏好模型在模式页（见 ModesView）。
// ---------------------------------------------------------------------------

const groups = ref<PromptGroup[]>([])
const lib = ref<PromptLib>({ categories: [], names: {} })

const keyword = ref('')
const promptKeyword = ref('')
const promptCategory = ref('')

// 组筛选放前端做：组本来就没几个，来回请求后端反而更慢
const visibleGroups = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return groups.value
  return groups.value.filter(
    (group) =>
      group.name.toLowerCase().includes(text) || group.description.toLowerCase().includes(text),
  )
})

/** 提示词库摊平成行，方便一张表过滤；类别单独留一列。 */
const promptRows = computed(() =>
  lib.value.categories.flatMap((category) =>
    (lib.value.names[category] ?? []).map((name) => ({ category, name })),
  ),
)

const visiblePrompts = computed(() =>
  promptRows.value.filter((item) => {
    if (promptCategory.value && item.category !== promptCategory.value) return false
    if (promptKeyword.value && !item.name.toLowerCase().includes(promptKeyword.value.toLowerCase()))
      return false
    return true
  }),
)

async function load(): Promise<void> {
  const [groupData, promptData] = await Promise.all([
    api.get<{ groups: PromptGroup[] }>('/prompt-groups'),
    api.get<PromptLib>('/prompts'),
  ])
  groups.value = groupData.groups
  lib.value = promptData
}

/** 组里引用了一个已经被删掉的提示词文件 —— 标出来，别让它悄悄失效。 */
function isMissing(category: string, name: string): boolean {
  return !(lib.value.names[category] ?? []).includes(name)
}

/**
 * 把 `{类别: 提示词名}` 摊成数组，供表格渲染。
 *
 * 不直接在模板里 `v-for="(name, category) in row.settings"`：遍历对象时键会被
 * 推断成 number，传给 isMissing 要一路 cast。摊平成数组后两边都是干净的 string。
 */
function settingsOf(settings: Record<string, string>): { category: string; name: string }[] {
  return Object.entries(settings ?? {}).map(([category, name]) => ({ category, name }))
}

// ---------------------------------------------------------------------------
// 提示词组：新建 / 编辑共用一个弹窗
// ---------------------------------------------------------------------------

const dialogOpen = ref(false)
/** 正在编辑的组 id；空串表示新建。 */
const editingId = ref('')
const form = reactive({
  name: '',
  description: '',
  settings: {} as Record<string, string>,
})

function openCreate(): void {
  editingId.value = ''
  form.name = ''
  form.description = ''
  // 六类都预置成空串：不选中任何提示词是合法状态，界面上要看得出来「可选但没选」，
  // 而不是一个 undefined 让下拉框显示空白
  form.settings = Object.fromEntries(lib.value.categories.map((item) => [item, '']))
  dialogOpen.value = true
}

// 参数按字段拆开而不是收整个对象：`el-table` 插槽里的 `row` 类型是宽泛的
// `DefaultRow`，直接传给要求 PromptGroup 的函数过不了类型检查
function openEdit(id: string, name: string, description: string, settings: Record<string, string>) {
  editingId.value = id
  form.name = name
  form.description = description
  form.settings = Object.fromEntries(
    lib.value.categories.map((item) => [item, settings[item] ?? '']),
  )
  dialogOpen.value = true
}

const canSubmit = computed(() => Boolean(form.name.trim() && form.description.trim()))

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  // 空串是「这一类不选」，提交前剔掉 —— 留着会让后端以为选了个叫空字符串的文件
  const settings: Record<string, string> = {}
  for (const [category, name] of Object.entries(form.settings)) {
    if (name) settings[category] = name
  }

  const payload = {
    name: form.name.trim(),
    description: form.description.trim(),
    settings,
  }

  try {
    if (editingId.value) {
      await api.put(`/prompt-groups/${editingId.value}`, payload)
      ElMessage.success('提示词组已更新')
    } else {
      await api.post('/prompt-groups', payload)
      ElMessage.success('提示词组已创建')
    }
    dialogOpen.value = false
    await load()
    // 任务页工具栏的「模式」选择器读的是同一份数据，改完得让它看到最新的，
    // 否则选中项会指向刚被删掉的组
    await loadOptions()
  } catch (exc) {
    // 重名、提示词不存在：后端的文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  }
}

async function remove(id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除提示词组「${name}」？`, '删除提示词组', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/prompt-groups/${id}`)
  await load()
  await loadOptions()
  ElMessage.success('已删除')
}

// ---------------------------------------------------------------------------
// 提示词正文：展开某一行时按需取，取过一次就缓存住
// ---------------------------------------------------------------------------

const contents = ref<Record<string, string>>({})

function contentKey(category: string, name: string): string {
  return `${category}/${name}`
}

/**
 * 展开某一行时按需取正文。
 *
 * 两个参数的类型都很宽，是被 `el-table` 的事件签名逼的：行数据是 `DefaultRow`，
 * 第二个参数则有两种形态 —— 带展开列时给「当前展开的行数组」，不带时给布尔值，
 * 所以类型定义是两者的交叉，只能收窄着用。
 */
async function onExpand(row: { category: string; name: string }, expanded: unknown): Promise<void> {
  const isOpen = Array.isArray(expanded)
    ? expanded.some(
        (item) =>
          (item as { category?: string })?.category === row.category &&
          (item as { name?: string })?.name === row.name,
      )
    : Boolean(expanded)

  const key = contentKey(row.category, row.name)
  if (!isOpen || contents.value[key]) return

  const data = await api.get<{ content: string }>(
    `/prompts/${encodeURIComponent(row.category)}/${encodeURIComponent(row.name)}`,
  )
  contents.value[key] = data.content
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>提示词</h1>
      <span class="hint">
        提示词组是从六类提示词里各挑一个拼成的搭配方案，是模式的组成部分之一；正文在 prompt/
        目录里维护，下面只读
      </span>
    </Teleport>

    <!-- 上半：提示词组 -->
    <section class="half">
      <div class="half-head">
        <h2>提示词组</h2>
        <el-input
          v-model="keyword"
          size="small"
          placeholder="按名称或简介过滤"
          clearable
          class="group-filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">
          新建提示词组
        </el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visibleGroups" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column prop="name" label="组名" width="140">
            <template #default="{ row }">
              <span class="group-name">{{ row.name }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="description" label="功能简介" min-width="150" show-overflow-tooltip />

          <el-table-column label="提示词设置" min-width="240">
            <template #default="{ row }">
              <!-- 空设置是合法状态：那就是不带任何系统提示词的纯问答 ——
                   必须显式说出来，否则看起来像漏填了 -->
              <span v-if="!settingsOf(row.settings).length" class="muted">
                （空 —— 不带系统提示词）
              </span>
              <template v-else>
                <el-tag
                  v-for="item in settingsOf(row.settings)"
                  :key="item.category"
                  size="small"
                  effect="plain"
                  :type="isMissing(item.category, item.name) ? 'danger' : 'info'"
                  class="set-tag"
                >
                  {{ item.category }}：{{ item.name }}
                  <template v-if="isMissing(item.category, item.name)">（文件缺失）</template>
                </el-tag>
              </template>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="openEdit(row.id, row.name, row.description, row.settings)"
              >
                编辑
              </el-button>
              <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
                删除
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="还没有提示词组；点右上角「新建提示词组」创建" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 下半：提示词正文（只读） -->
    <section class="half">
      <div class="half-head">
        <h2>提示词库</h2>
        <el-input
          v-model="promptKeyword"
          size="small"
          placeholder="按名称过滤"
          clearable
          class="filter"
        />
        <el-select
          v-model="promptCategory"
          size="small"
          placeholder="全部类别"
          clearable
          class="filter"
        >
          <el-option v-for="item in lib.categories" :key="item" :label="item" :value="item" />
        </el-select>
        <span class="readonly muted">正文只读：要改就去 prompt/ 目录编辑 .md 文件</span>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visiblePrompts" size="small" stripe @expand-change="onExpand">
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="prompt-body">{{ contents[contentKey(row.category, row.name)] ?? '加载中…' }}</pre>
            </template>
          </el-table-column>

          <el-table-column prop="category" label="类别" width="110" />
          <el-table-column prop="name" label="提示词名" min-width="200" />

          <template #empty>
            <el-empty description="prompt/ 目录下还没有提示词" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗。编辑复用同一张表单，只多带一份初值 -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId ? '编辑提示词组' : '新建提示词组'"
      width="520px"
    >
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item label="组名" required>
          <el-input v-model="form.name" placeholder="例如：严谨分析" maxlength="30" />
        </el-form-item>

        <el-form-item label="功能简介" required>
          <el-input
            v-model="form.description"
            placeholder="一句话说明这组提示词用来做什么"
            maxlength="60"
          />
        </el-form-item>

        <el-divider content-position="left">提示词搭配</el-divider>

        <!-- 六类各一个下拉。每一类都可以不选：不是每个任务都需要一整套提示词 -->
        <el-form-item v-for="category in lib.categories" :key="category" :label="category">
          <el-select
            v-model="form.settings[category]"
            clearable
            :placeholder="`不选用${category}`"
            class="wide"
          >
            <el-option
              v-for="name in lib.names[category] ?? []"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">取消</el-button>
        <el-button size="small" type="primary" :disabled="!canSubmit" @click="submit">
          {{ editingId ? '保存' : '创建' }}
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
  font-size: 14px;
  font-weight: 600;
}

/* 搜索框吃掉剩余宽度，按钮贴右 */
.group-filter {
  flex: 1;
  max-width: 260px;
  margin-left: auto;
}

.filter {
  width: 150px;
}

/* 表格区在 half 里滚动，而不是让整个页面滚 */
.half-body {
  flex: 1;
  min-height: 0;
}

.group-name {
  font-weight: 500;
}

.set-tag {
  margin: 0 4px 2px 0;
}

.readonly {
  font-size: 12px;
}

.field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
}

.wide {
  width: 100%;
}

/* 提示词正文是 .md 原文，等宽字体保留原始缩进和换行 */
.prompt-body {
  max-height: 320px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bg-soft);
  border-radius: 6px;
}
</style>
