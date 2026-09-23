<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, defineAsyncComponent, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { PromptGroup, PromptItem, PromptLib } from '../api/types'
import { loadOptions } from '../stores/session'
import { errorText } from '../utils/error'

const { t } = useI18n()

// 编辑器带着 CodeMirror（几百 KB），而只有真正打开弹窗时才需要它 ——
// 异步加载能让这两个页面本身的包保持干净
const MarkdownEditor = defineAsyncComponent(() => import('../components/MarkdownEditor.vue'))

// ---------------------------------------------------------------------------
// 数据
//
// 一个提示词组 = 从六类提示词里各挑一个（可以都不挑）。模式现在指三类组 + 记忆的
// 组合，这一页只管提示词那一类 —— 偏好模型在模式页（见 ModesView）。
// ---------------------------------------------------------------------------

const groups = ref<PromptGroup[]>([])
const lib = ref<PromptLib>({ categories: [], items: [] })

const keyword = ref('')
const promptKeyword = ref('')
/**
 * 提示词库当前看的是哪一类。
 *
 * 类别是固定的六类，所以界面上铺成六个按钮、单选 —— 默认停在第一类，具体是哪个
 * 要等后端给了清单才知道（见 `load`）。
 */
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

/** 提示词库的筛选（名称 + 类别）。数据本身已按「分类顺序 + 名字」排好。 */
const visiblePrompts = computed(() =>
  lib.value.items.filter((item) => {
    if (promptCategory.value && item.category !== promptCategory.value) return false
    if (promptKeyword.value && !item.name.toLowerCase().includes(promptKeyword.value.toLowerCase()))
      return false
    return true
  }),
)

/** id -> 条目：渲染「组里引用的提示词」、按分类取用都靠它。 */
const itemById = computed(() => new Map(lib.value.items.map((item) => [item.id, item])))

async function load(): Promise<void> {
  const [groupData, promptData] = await Promise.all([
    api.get<{ groups: PromptGroup[] }>('/prompt-groups'),
    api.get<PromptLib>('/prompts'),
  ])
  groups.value = groupData.groups
  lib.value = promptData
  // 类别按钮默认停在第一类。放在这儿而不是 ref 的初值里：类别清单是后端给的，
  // 要等它回来才知道第一类叫什么 —— 写死「身份」的话，后端哪天调了顺序就错位了
  if (!promptCategory.value) promptCategory.value = promptData.categories[0] ?? ''
}

/** 某个类别下有哪些提示词（组编辑弹窗按类别分组渲染用）。 */
function itemsOfCategory(category: string): PromptItem[] {
  return lib.value.items.filter((item) => item.category === category)
}

/**
 * 把组里引用的 id 摊成可渲染的标签；已经被删掉的标出来，别让它悄悄失效。
 *
 * 摊平一次而不是在模板里查两层：模板里既要拿名字、又要判断在不在，写起来很绕。
 */
function refsOf(ids: string[]): { id: string; label: string; missing: boolean }[] {
  return ids.map((id) => {
    const item = itemById.value.get(id)
    return item
      ? { id, label: `${item.category}：${item.name}`, missing: false }
      : { id, label: t('prompts.deletedMissing', { id }), missing: true }
  })
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
  /** 选中的提示词 id。界面上按类别分组显示，底层就是一个列表。 */
  prompts: [] as string[],
})

/** 当前表单里选中的、属于某个类别的 id —— 多选框绑的是它。 */
function formIdsOf(category: string): string[] {
  return form.prompts.filter((id) => itemById.value.get(id)?.category === category)
}

/**
 * 换掉某个类别的选择，别的类别不动。
 *
 * 只能这样「替换一段」而不是双向绑定整个列表：多选框只知道本类别撤了什么、加了什么，
 * 若直接把 `form.prompts` 换掉，别的类别的选择会被一起清空。
 */
function setFormCategory(category: string, ids: string[]): void {
  const others = form.prompts.filter((id) => itemById.value.get(id)?.category !== category)
  form.prompts = [...others, ...ids]
}

function openCreate(): void {
  editingId.value = ''
  form.name = ''
  form.description = ''
  // 一条都不选是合法状态：那就退化成不带任何系统提示词的纯问答
  form.prompts = []
  dialogOpen.value = true
}

// 参数按字段拆开而不是收整个对象：`el-table` 插槽里的 `row` 类型是宽泛的
// `DefaultRow`，直接传给要求 PromptGroup 的函数过不了类型检查
function openEdit(id: string, name: string, description: string, prompts: string[]) {
  editingId.value = id
  form.name = name
  form.description = description
  // 拷一份：直接绑原数组的话，取消编辑也会改动列表里的数据
  form.prompts = [...prompts]
  dialogOpen.value = true
}

const canSubmit = computed(() => Boolean(form.name.trim() && form.description.trim()))

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  const payload = {
    name: form.name.trim(),
    description: form.description.trim(),
    prompts: [...form.prompts],
  }

  try {
    if (editingId.value) {
      await api.put(`/prompt-groups/${editingId.value}`, payload)
      ElMessage.success(t('prompts.groupUpdated'))
    } else {
      await api.post('/prompt-groups', payload)
      ElMessage.success(t('prompts.groupCreated'))
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
    await ElMessageBox.confirm(t('prompts.deleteGroupConfirm', { name }), t('prompts.deleteGroupTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/prompt-groups/${id}`)
  await load()
  await loadOptions()
  ElMessage.success(t('common.deleted'))
}

// ---------------------------------------------------------------------------
// 提示词正文：展开某一行时按需取，取过一次就缓存住（键就是 id）
// ---------------------------------------------------------------------------

const contents = ref<Record<string, string>>({})

// ---------------------------------------------------------------------------
// 提示词正文：新建 / 编辑共用一个弹窗
//
// **名字与分类都可以改**：它们只是元信息，引用用的是 id（见 quill_agent.prompts）——
// 从前名字就是文件名，改个名等于换了个标识，所有引用会一起失效；现在不会了。
// ---------------------------------------------------------------------------

/** 正在编辑的提示词 id；空串表示新建。 */
const editingPrompt = ref('')
const promptDialogOpen = ref(false)
const promptSaving = ref(false)
const promptForm = reactive({ category: '', name: '', content: '' })

function openPromptCreate(): void {
  editingPrompt.value = ''
  promptForm.category = lib.value.categories[0] ?? ''
  promptForm.name = ''
  promptForm.content = ''
  promptDialogOpen.value = true
}

async function openPromptEdit(item: { id: string }): Promise<void> {
  editingPrompt.value = item.id
  promptForm.category = ''
  promptForm.name = ''
  promptForm.content = ''
  promptDialogOpen.value = true

  try {
    const data = await api.get<{ name: string; category: string; content: string }>(
      `/prompts/${encodeURIComponent(item.id)}`,
    )
    promptForm.name = data.name
    promptForm.category = data.category
    promptForm.content = data.content
    // 顺手把展开区里缓存的那份也对齐，免得两个地方显示不一样的正文
    contents.value[item.id] = data.content
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
}

const canSubmitPrompt = computed(
  () => Boolean(promptForm.name.trim() && promptForm.category && promptForm.content.trim()),
)

async function submitPrompt(): Promise<void> {
  if (!canSubmitPrompt.value) return

  promptSaving.value = true
  try {
    const payload = {
      name: promptForm.name.trim(),
      category: promptForm.category,
      content: promptForm.content,
    }

    if (editingPrompt.value) {
      await api.put(`/prompts/${encodeURIComponent(editingPrompt.value)}`, payload)
      ElMessage.success(t('prompts.saved'))
    } else {
      await api.post('/prompts', payload)
      ElMessage.success(t('prompts.created'))
    }

    promptDialogOpen.value = false
    await load()
    // 提示词组的标签与下拉读的是同一份数据，改完得让它看到最新的
    await loadOptions()
  } catch (exc) {
    // 名字为空/超长、分类非法：后端文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  } finally {
    promptSaving.value = false
  }
}

/**
 * 删除一条提示词。
 *
 * 删之前先把「还有哪些提示词组在引用它」写进确认框：引用按 id 存，删掉之后后端会
 * 顺手把它从那些组里摘掉（级联，见 `delete_prompt`）。先告诉用户比让他事后自己
 * 发现好 —— 那个组少了一条，他是不会立刻注意到的。
 */
async function removePrompt(item: { id: string; name: string }): Promise<void> {
  const usedBy = groups.value
    .filter((group) => group.prompts.includes(item.id))
    .map((group) => group.name)

  const hint = usedBy.length
    ? t('prompts.deletePromptUsed', { count: usedBy.length, groups: usedBy.join('、') })
    : t('prompts.deletePromptUnused')

  try {
    await ElMessageBox.confirm(
      t('prompts.deletePromptConfirm', { name: item.name, hint }),
      t('prompts.deletePromptTitle'),
      {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户按了取消
  }

  let touched: string[] = []
  try {
    // 后端会把它从引用它的组里摘掉，并把动过的组名回传 —— 顺手告诉用户动了哪几个，
    // 不然他只知道「删了」，不知道某个组的提示词少了一条
    const result = await api.del<{ removed: boolean; used_by: string[] }>(
      `/prompts/${encodeURIComponent(item.id)}`,
    )
    touched = result.used_by ?? []
  } catch (exc) {
    ElMessage.error(errorText(exc))
    return
  }

  // 展开区里缓存过它的正文，一并清掉，免得再点开时显示已删除的内容
  delete contents.value[item.id]

  await load()
  await loadOptions()
  ElMessage.success(
    touched.length
      ? t('prompts.deletedAndRemoved', { count: touched.length, groups: touched.join('、') })
      : t('common.deleted'),
  )
}

/**
 * 展开某一行时按需取正文。
 *
 * 两个参数的类型都很宽，是被 `el-table` 的事件签名逼的：行数据是 `DefaultRow`，
 * 第二个参数则有两种形态 —— 带展开列时给「当前展开的行数组」，不带时给布尔值，
 * 所以类型定义是两者的交叉，只能收窄着用。
 */
async function onExpand(row: { id: string }, expanded: unknown): Promise<void> {
  const isOpen = Array.isArray(expanded)
    ? expanded.some((item) => (item as { id?: string })?.id === row.id)
    : Boolean(expanded)

  if (!isOpen || contents.value[row.id]) return

  const data = await api.get<{ content: string }>(`/prompts/${encodeURIComponent(row.id)}`)
  contents.value[row.id] = data.content
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>{{ t('prompts.title') }}</h1>
      <span class="hint">
        {{ t('prompts.hint') }}
      </span>
    </Teleport>

    <!-- 上半：提示词组 -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('prompts.groupTitle') }}</h2>
        <el-input
          v-model="keyword"
          size="small"
          :placeholder="t('prompts.groupFilter')"
          clearable
          class="group-filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">
          {{ t('prompts.groupCreate') }}
        </el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visibleGroups" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column :label="t('prompts.columnGroupName')" prop="name" width="180">
            <template #default="{ row }">
              <span class="group-name">{{ row.name }}</span>
              <el-tag v-if="row.builtin" size="small" effect="plain" class="builtin">{{ t('prompts.builtin') }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="t('prompts.columnDesc')" prop="description" min-width="150" show-overflow-tooltip />

          <el-table-column :label="t('prompts.columnPrompts')" min-width="240">
            <template #default="{ row }">
              <!-- 一条都不选是合法状态：那就是不带任何系统提示词的纯问答 ——
                   必须显式说出来，否则看起来像漏填了 -->
              <span v-if="!refsOf(row.prompts).length" class="muted">
                {{ t('prompts.groupEmptyPrompts') }}
              </span>
              <template v-else>
                <el-tag
                  v-for="item in refsOf(row.prompts)"
                  :key="item.id"
                  size="small"
                  effect="plain"
                  :type="item.missing ? 'danger' : 'info'"
                  class="set-tag"
                >
                  {{ item.label }}
                </el-tag>
              </template>
            </template>
          </el-table-column>

          <el-table-column :label="t('prompts.columnActions')" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="openEdit(row.id, row.name, row.description, row.prompts)"
              >
                {{ t('common.edit') }}
              </el-button>
              <!-- 内置项不给删除入口（编辑照旧留着）。真正的拒绝在存储层（见
                   quill_agent/defaults.py）—— 这里少了判断，用户点了只会拿到一句 400 -->
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
            <el-empty :description="t('prompts.groupEmpty')" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 下半：提示词正文（只读） -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('prompts.libTitle') }}</h2>

        <!-- 六个类别铺成六个按钮，紧跟标题。
             原来是下拉：要点开、看清、再选，三步；而类别总共只有六个、还是固定不变的
             六类 —— 铺开一步就到，也顺带让人一眼看完提示词是按哪六类组织的 -->
        <el-radio-group v-model="promptCategory" size="small" class="cats">
          <el-radio-button v-for="item in lib.categories" :key="item" :value="item">
            {{ item }}
          </el-radio-button>
        </el-radio-group>

        <el-input
          v-model="promptKeyword"
          size="small"
          :placeholder="t('prompts.libFilter')"
          clearable
          class="filter"
        />
        <el-button
          size="small"
          type="primary"
          :icon="Plus"
          class="add-btn"
          @click="openPromptCreate"
        >
          {{ t('prompts.addPrompt') }}
        </el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visiblePrompts" size="small" stripe @expand-change="onExpand">
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="prompt-body">{{ contents[row.id] ?? t('prompts.loading') }}</pre>
            </template>
          </el-table-column>

          <el-table-column :label="t('prompts.columnCategory')" prop="category" width="110" />
          <el-table-column :label="t('prompts.columnName')" prop="name" min-width="200">
            <template #default="{ row }">
              <span>{{ row.name }}</span>
              <el-tag v-if="row.builtin" size="small" effect="plain" class="builtin">{{ t('prompts.builtin') }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="t('prompts.columnActions')" width="130" align="center">
            <template #default="{ row }">
              <!-- 只传行内字段：row 的类型是宽泛的 DefaultRow，整个对象传不进具体类型 -->
              <el-button size="small" text type="primary" @click="openPromptEdit({ id: row.id })">
                {{ t('common.edit') }}
              </el-button>
              <!-- 内置提示词不给删除入口：它是出厂内容，而出厂的那个提示词组正引用着
                   它，删掉之后整组就散了。**编辑照旧留着** —— 改措辞是正当需求，
                   这条规矩只是不许删 -->
              <el-button
                v-if="!row.builtin"
                size="small"
                text
                type="danger"
                @click="removePrompt({ id: row.id, name: row.name })"
              >
                {{ t('common.delete') }}
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty
              :description="t('prompts.libEmpty')"
              :image-size="60"
            />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗。编辑复用同一张表单，只多带一份初值 -->
    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId ? t('prompts.groupForm.editTitle') : t('prompts.groupForm.createTitle')"
      width="520px"
      append-to-body
    >
      <el-form label-width="90px" size="default" @submit.prevent>
        <el-form-item :label="t('prompts.groupForm.name')" required>
          <el-input v-model="form.name" :placeholder="t('prompts.groupForm.namePlaceholder')" maxlength="30" />
        </el-form-item>

        <el-form-item :label="t('prompts.groupForm.desc')" required>
          <el-input
            v-model="form.description"
            :placeholder="t('prompts.groupForm.descPlaceholder')"
            maxlength="60"
          />
        </el-form-item>

        <el-divider content-position="left">{{ t('prompts.groupForm.pairing') }}</el-divider>

        <!-- 六类各一个多选框。每类都可以不选，也可以选多条 —— 引用用的是 id，
             不再有「一个分类只能选一条」的限制 -->
        <el-form-item v-for="category in lib.categories" :key="category" :label="category">
          <el-select
            :model-value="formIdsOf(category)"
            multiple
            collapse-tags
            :placeholder="t('prompts.groupForm.notChosen', { category })"
            class="wide"
            @update:model-value="setFormCategory(category, $event)"
          >
            <el-option
              v-for="item in itemsOfCategory(category)"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button size="small" type="primary" :disabled="!canSubmit" @click="submit">
          {{ editingId ? t('common.save') : t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 提示词正文：新建 / 编辑共用。名字与分类都可以改 —— 它们只是元信息，
         引用用的是 id，改完不影响任何已有的提示词组 -->
    <el-dialog
      v-model="promptDialogOpen"
      :title="editingPrompt ? t('prompts.promptForm.editTitle') : t('prompts.promptForm.addTitle')"
      width="760px"
      top="6vh"
      append-to-body
    >
      <el-form label-width="70px" size="default" @submit.prevent>
        <el-form-item :label="t('prompts.promptForm.name')" required>
          <el-input v-model="promptForm.name" :placeholder="t('prompts.promptForm.namePlaceholder')" maxlength="60" />
          <div class="field-hint muted">
            {{ t('prompts.promptForm.nameHint') }}
          </div>
        </el-form-item>

        <el-form-item :label="t('prompts.promptForm.category')" required>
          <el-select v-model="promptForm.category" class="wide">
            <el-option v-for="item in lib.categories" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('prompts.promptForm.body')" required>
          <MarkdownEditor
            v-model="promptForm.content"
            :placeholder="t('prompts.promptForm.bodyPlaceholder')"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="promptDialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="promptSaving"
          :disabled="!canSubmitPrompt"
          @click="submitPrompt"
        >
          {{ t('common.confirm') }}
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
  /* 六个类别按钮铺开后这一行明显变长：允许换行，而不是把「添加提示词」挤出屏幕 */
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

/* 六个类别按钮，紧跟标题铺成一排 */
.cats {
  flex-shrink: 0;
}

/* 六个按钮要和同排的「按名称过滤」输入框**一样高**。
 *
 * 单选按钮的高度靠内边距撑，而输入框是固定高度（`--el-component-size-small`，24px）——
 * 只收内边距的话它们会比输入框矮一截，六个数挨着排看着就不齐。所以直接对齐那个变量，
 * 高度就跟着主题和尺寸走，不用写死像素。 */
.cats :deep(.el-radio-button__inner) {
  display: flex;
  align-items: center;
  height: var(--el-component-size-small);
  padding: 0 12px;
}

.half-head h2 {
  margin: 0;
  font-size: var(--fs-base);
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

/* 「添加提示词」贴到这一行最右侧 —— 和上半的「新建提示词组」同一个位置 */
.add-btn {
  margin-left: auto;
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

.field-hint {
  margin-top: 4px;
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bg-soft);
  border-radius: 6px;
}
</style>
