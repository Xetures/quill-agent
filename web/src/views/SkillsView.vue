<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, defineAsyncComponent, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { SkillGroup, SkillItem } from '../api/types'
import { errorText } from '../utils/error'

const { t } = useI18n()

// 编辑器带着 CodeMirror（几百 KB），而只有真正打开弹窗时才需要它 ——
// 异步加载能让这两个页面本身的包保持干净
const MarkdownEditor = defineAsyncComponent(() => import('../components/MarkdownEditor.vue'))

// ---------------------------------------------------------------------------
// 数据
// ---------------------------------------------------------------------------

const skills = ref<SkillItem[]>([])
const groups = ref<SkillGroup[]>([])
const dir = ref('')
/** 技能名 -> 正文。展开时才去取，列表接口不返回正文（它可能上千字）。 */
const contents = ref<Record<string, string>>({})
const groupKeyword = ref('')

const visibleGroups = computed(() => {
  const text = groupKeyword.value.trim().toLowerCase()
  if (!text) return groups.value
  return groups.value.filter(
    (group) =>
      group.name.toLowerCase().includes(text) || group.description.toLowerCase().includes(text),
  )
})

async function load(): Promise<void> {
  // 技能组与技能列表互不依赖，一次并发取回
  const [skillData, groupData] = await Promise.all([
    api.get<{ skills: SkillItem[]; dir: string }>('/skills'),
    api.get<{ groups: SkillGroup[] }>('/skill-groups'),
  ])
  skills.value = skillData.skills
  dir.value = skillData.dir
  groups.value = groupData.groups
}

/**
 * 展开某一行时按需取正文，取过一次就缓存住。
 *
 * 两个参数的类型都很宽，是被 `el-table` 的事件签名逼的：行数据是 `DefaultRow`，
 * 第二个参数则有两种形态 —— 带展开列时给「当前展开的行数组」，不带时给布尔值，
 * 所以类型定义是两者的交叉，只能收窄着用。
 */
async function onExpand(row: { name: string }, expanded: unknown): Promise<void> {
  const isOpen = Array.isArray(expanded)
    ? expanded.some((item) => (item as { name?: string })?.name === row.name)
    : Boolean(expanded)

  if (!isOpen || contents.value[row.name]) return

  const data = await api.get<{ content: string }>(`/skills/${encodeURIComponent(row.name)}`)
  contents.value[row.name] = data.content
}

// ---------------------------------------------------------------------------
// 技能正文：新建 / 编辑共用一个弹窗
//
// 界面上给的是「使用场景 + 正文」两个框，对应 SKILL.md 拆开后的两部分。
// **拼回文件由后端做**（`skills.compose_skill`）：元信息块只认单行 `键: 值`，
// 让用户直接编它，多写一行或缩进一下就会静默解析失败、description 丢掉 ——
// 而那正是模型判断「什么时候该用我」的唯一依据。
// ---------------------------------------------------------------------------

/** 正在编辑的技能名；空串表示新建。 */
const editingSkill = ref('')
const skillDialogOpen = ref(false)
const skillSaving = ref(false)
const skillForm = reactive({ name: '', description: '', content: '' })

function openSkillCreate(): void {
  editingSkill.value = ''
  skillForm.name = ''
  skillForm.description = ''
  skillForm.content = ''
  skillDialogOpen.value = true
}

async function openSkillEdit(name: string): Promise<void> {
  editingSkill.value = name
  skillForm.name = name
  skillForm.description = ''
  skillForm.content = ''
  skillDialogOpen.value = true

  try {
    const data = await api.get<{ description: string; content: string }>(
      `/skills/${encodeURIComponent(name)}`,
    )
    skillForm.description = data.description
    skillForm.content = data.content
    // 展开区里缓存的那份也对齐，免得两处显示不一样的正文
    contents.value[name] = data.content
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
}

// 三个字段全必填：名字是目录名；使用场景是模型判断「什么时候该加载我」的唯一依据；
// 正文为空的话这个技能就没有意义
const canSubmitSkill = computed(() =>
  Boolean(skillForm.name.trim() && skillForm.description.trim() && skillForm.content.trim()),
)

async function submitSkill(): Promise<void> {
  if (!canSubmitSkill.value) return

  skillSaving.value = true
  try {
    if (editingSkill.value) {
      await api.put(`/skills/${encodeURIComponent(editingSkill.value)}`, {
        description: skillForm.description.trim(),
        content: skillForm.content,
      })
      ElMessage.success(t('skills.saved'))
    } else {
      await api.post('/skills', {
        name: skillForm.name.trim(),
        description: skillForm.description.trim(),
        content: skillForm.content,
      })
      ElMessage.success(t('skills.created'))
    }

    skillDialogOpen.value = false
    await load()
  } catch (exc) {
    // 重名、名字不合法：后端文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  } finally {
    skillSaving.value = false
  }
}

// ---------------------------------------------------------------------------
// 技能组：新建 / 编辑共用一个弹窗（与工具页同一套路）
// ---------------------------------------------------------------------------

const dialogOpen = ref(false)
/** 正在编辑的组 id；空串表示新建。 */
const editingId = ref('')
const form = reactive({ name: '', description: '', skills: [] as string[] })

function openCreate(): void {
  editingId.value = ''
  form.name = ''
  form.description = ''
  form.skills = []
  dialogOpen.value = true
}

function openEdit(id: string, name: string, description: string, skillNames: string[]): void {
  editingId.value = id
  form.name = name
  form.description = description
  form.skills = [...skillNames]
  dialogOpen.value = true
}

const canSubmit = computed(() => Boolean(form.name.trim() && form.description.trim()))

/** 全选：一次把所有技能放进组里。技能总数不多，比逐个点省事。 */
function selectAllSkills(): void {
  form.skills = skills.value.map((skill) => skill.name)
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  const payload = {
    name: form.name.trim(),
    description: form.description.trim(),
    skills: form.skills,
  }

  try {
    if (editingId.value) {
      await api.put(`/skill-groups/${editingId.value}`, payload)
      ElMessage.success(t('skills.groupUpdated'))
    } else {
      await api.post('/skill-groups', payload)
      ElMessage.success(t('skills.groupCreated'))
    }
    dialogOpen.value = false
    await load()
  } catch (exc) {
    // 重名、未知技能名：后端文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  }
}

// 参数按字段拆开：el-table 插槽里的 row 是宽泛的 DefaultRow，
// 直接传整个对象过不了类型检查（openEdit 同理）
async function remove(id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(t('skills.deleteGroupConfirm', { name }), t('skills.deleteGroupTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/skill-groups/${id}`)
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
      <h1>{{ t('skills.title') }}</h1>
      <span class="hint" v-html="t('skills.intro', { dir })" />
    </Teleport>

    <!-- 上半：技能组 -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('skills.groupTitle') }}</h2>
        <el-input
          v-model="groupKeyword"
          size="small"
          :placeholder="t('skills.groupFilter')"
          clearable
          class="group-filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">{{ t('skills.groupCreate') }}</el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visibleGroups" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column prop="name" :label="t('skills.columnGroupName')" width="180">
            <template #default="{ row }">
              <span class="group-name">{{ row.name }}</span>
              <el-tag v-if="row.builtin" size="small" effect="plain" class="builtin">{{ t('skills.builtin') }}</el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="description" :label="t('skills.columnDesc')" min-width="180" show-overflow-tooltip />

          <el-table-column :label="t('skills.columnSkills')" min-width="200">
            <template #default="{ row }">
              <!-- 空列表是合法状态，必须显式说出来，否则看起来像漏填了 -->
              <span v-if="!row.skills.length" class="muted">{{ t('skills.groupEmptySkills') }}</span>
              <el-tag v-for="name in row.skills" :key="name" size="small" class="skill-tag">
                {{ name }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column :label="t('skills.columnActions')" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="openEdit(row.id, row.name, row.description, row.skills)"
              >
                {{ t('common.edit') }}
              </el-button>
              <!-- 内置项不给删除入口，编辑照旧留着（硬约束在存储层，见
                   quill_agent/defaults.py） -->
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
            <el-empty :description="t('skills.groupEmpty')" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 下半：单个技能（含展开看正文、编辑弹窗） -->
    <section class="half">
      <div class="half-head">
        <h2>{{ t('skills.allTitle') }}</h2>
        <el-button
          size="small"
          type="primary"
          :icon="Plus"
          class="add-btn"
          @click="openSkillCreate"
        >
          {{ t('skills.addSkill') }}
        </el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="skills" size="small" stripe @expand-change="onExpand">
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="mono body">{{ contents[row.name] ?? t('skills.loading') }}</pre>
            </template>
          </el-table-column>

          <el-table-column prop="name" :label="t('skills.columnName')" width="140" />
          <el-table-column :label="t('skills.columnUsage')">
            <template #default="{ row }">
              <span :class="{ muted: !row.description }">
                {{ row.description || t('skills.noUsage') }}
              </span>
            </template>
          </el-table-column>

          <!-- 这里不再有「启用」开关：给不给技能由模式的技能组决定 -->

          <el-table-column :label="t('skills.columnActions')" width="80" align="center">
            <template #default="{ row }">
              <!-- 只传名字：row 的类型是宽泛的 DefaultRow -->
              <el-button size="small" text type="primary" @click="openSkillEdit(row.name)">
                {{ t('common.edit') }}
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty
              :description="t('skills.skillsEmpty')"
              :image-size="60"
            />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗 -->
    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialogOpen"
      :title="editingId ? t('skills.groupForm.editTitle') : t('skills.groupForm.createTitle')"
      width="480px"
      append-to-body
    >
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item :label="t('skills.groupForm.name')" required>
          <el-input v-model="form.name" :placeholder="t('skills.groupForm.namePlaceholder')" maxlength="30" />
        </el-form-item>

        <el-form-item :label="t('skills.groupForm.desc')" required>
          <el-input
            v-model="form.description"
            :placeholder="t('skills.groupForm.descPlaceholder')"
            maxlength="60"
          />
        </el-form-item>

        <el-form-item :label="t('skills.groupForm.skills')">
          <div class="select-block">
            <div class="select-bar">
              <span class="muted count">{{ t('skills.groupForm.count', { selected: form.skills.length, total: skills.length }) }}</span>
              <el-button link size="small" type="primary" @click="selectAllSkills">{{ t('skills.groupForm.selectAll') }}</el-button>
              <el-button link size="small" @click="form.skills = []">{{ t('skills.groupForm.clear') }}</el-button>
            </div>

            <el-select
              v-model="form.skills"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :placeholder="t('skills.groupForm.selectPlaceholder')"
              class="skill-select"
            >
              <el-option
                v-for="skill in skills"
                :key="skill.name"
                :label="skill.name"
                :value="skill.name"
              />
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

    <!-- 技能正文：新建 / 编辑共用。名字在编辑时锁住 —— 它是技能组里的引用标识 -->
    <el-dialog
      v-model="skillDialogOpen"
      :title="editingSkill ? t('skills.skillForm.editTitle') : t('skills.skillForm.addTitle')"
      width="760px"
      top="6vh"
      append-to-body
    >
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item :label="t('skills.skillForm.name')" required>
          <el-input
            v-model="skillForm.name"
            :disabled="Boolean(editingSkill)"
            :placeholder="t('skills.skillForm.namePlaceholder')"
            maxlength="60"
          />
          <div v-if="editingSkill" class="field-hint muted">
            {{ t('skills.skillForm.nameHint') }}
          </div>
        </el-form-item>

        <el-form-item :label="t('skills.skillForm.usage')" required>
          <el-input
            v-model="skillForm.description"
            :placeholder="t('skills.skillForm.usagePlaceholder')"
            maxlength="120"
          />
          <div class="field-hint muted">
            {{ t('skills.skillForm.usageHint') }}
          </div>
        </el-form-item>

        <el-form-item :label="t('skills.skillForm.content')" required>
          <MarkdownEditor
            v-model="skillForm.content"
            :placeholder="t('skills.skillForm.contentPlaceholder')"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="skillDialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="skillSaving"
          :disabled="!canSubmitSkill"
          @click="submitSkill"
        >
          {{ t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 上下两半平均分：页面高度固定（.page 撑满主区），两个 half 各占一半，
 * 各自的表格在内部滚动 —— 与工具页保持一致 */
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

.group-filter {
  flex: 1;
  max-width: 260px;
  margin-left: auto;
}

/* 「添加技能」贴到这一行最右侧 —— 和上半的「新建技能组」同一个位置 */
.add-btn {
  margin-left: auto;
}

.field-hint {
  margin-top: 4px;
  font-size: var(--fs-xs);
  line-height: 1.4;
}

.half-body {
  flex: 1;
  min-height: 0;
}

.group-name {
  font-weight: 500;
}

.skill-tag {
  margin: 0 4px 2px 0;
}

.skill-select {
  width: 100%;
}

/* 技能列表：一行「已选 x/y」+ 全选 / 清空，再下面是多选下拉（与工具页一致） */
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

.body {
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--bg-soft);
  white-space: pre-wrap;
  max-height: 340px;
  overflow: auto;
}
</style>
