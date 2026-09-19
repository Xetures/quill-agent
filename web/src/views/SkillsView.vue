<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import type { SkillGroup, SkillItem } from '../api/types'
import { errorText } from '../utils/error'

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
      ElMessage.success('技能组已更新')
    } else {
      await api.post('/skill-groups', payload)
      ElMessage.success('技能组已创建')
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
    await ElMessageBox.confirm(`删除技能组「${name}」？`, '删除技能组', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户按了取消
  }

  await api.del(`/skill-groups/${id}`)
  await load()
  ElMessage.success('已删除')
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>技能</h1>
      <span class="hint">
        正文放在 <code class="mono">{{ dir }}/&lt;技能名&gt;/SKILL.md</code>；技能组是技能的搭配方案，是模式的一部分，启用与否由模式决定
      </span>
    </Teleport>

    <!-- 上半：技能组 -->
    <section class="half">
      <div class="half-head">
        <h2>技能组</h2>
        <el-input
          v-model="groupKeyword"
          size="small"
          placeholder="按名称或简介过滤"
          clearable
          class="group-filter"
        />
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建技能组</el-button>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="visibleGroups" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />

          <el-table-column prop="name" label="技能组名" width="140">
            <template #default="{ row }">
              <span class="group-name">{{ row.name }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="description" label="功能简介" min-width="180" show-overflow-tooltip />

          <el-table-column label="技能列表" min-width="200">
            <template #default="{ row }">
              <!-- 空列表是合法状态，必须显式说出来，否则看起来像漏填了 -->
              <span v-if="!row.skills.length" class="muted">（空 —— 不给模型任何技能）</span>
              <el-tag v-for="name in row.skills" :key="name" size="small" class="skill-tag">
                {{ name }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="130" align="center">
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="openEdit(row.id, row.name, row.description, row.skills)"
              >
                编辑
              </el-button>
              <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
                删除
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="还没有技能组；点右上角「新建技能组」创建" :image-size="60" />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 下半：单个技能（原有功能，含展开看正文） -->
    <section class="half">
      <div class="half-head">
        <h2>全部技能</h2>
      </div>

      <el-scrollbar class="half-body">
        <el-table :data="skills" size="small" stripe @expand-change="onExpand">
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="mono body">{{ contents[row.name] ?? '（加载中…）' }}</pre>
            </template>
          </el-table-column>

          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column label="适用场景">
            <template #default="{ row }">
              <span :class="{ muted: !row.description }">
                {{ row.description || '（未填写，模型看不出什么时候该用它）' }}
              </span>
            </template>
          </el-table-column>

          <!-- 这里不再有「启用」开关：给不给技能由模式的技能组决定 -->

          <template #empty>
            <el-empty
              description="还没有技能：在 skills/ 下建个目录、放个 SKILL.md"
              :image-size="60"
            />
          </template>
        </el-table>
      </el-scrollbar>
    </section>

    <!-- 新建 / 编辑弹窗 -->
    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑技能组' : '新建技能组'" width="480px">
      <el-form label-width="80px" size="default" @submit.prevent>
        <el-form-item label="技能组名" required>
          <el-input v-model="form.name" placeholder="例如：写作相关" maxlength="30" />
        </el-form-item>

        <el-form-item label="功能简介" required>
          <el-input
            v-model="form.description"
            placeholder="一句话说明这组技能用来做什么"
            maxlength="60"
          />
        </el-form-item>

        <el-form-item label="技能列表">
          <div class="select-block">
            <div class="select-bar">
              <span class="muted count">已选 {{ form.skills.length }} / {{ skills.length }}</span>
              <el-button link size="small" type="primary" @click="selectAllSkills">全选</el-button>
              <el-button link size="small" @click="form.skills = []">清空</el-button>
            </div>

            <el-select
              v-model="form.skills"
              multiple
              collapse-tags
              collapse-tags-tooltip
              placeholder="选择组内技能（可不选：空组 = 不给模型技能）"
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
  font-size: 14px;
  font-weight: 600;
}

.group-filter {
  flex: 1;
  max-width: 260px;
  margin-left: auto;
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
  font-size: 12px;
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
