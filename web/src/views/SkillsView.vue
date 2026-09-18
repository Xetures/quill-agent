<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { SkillItem } from '../api/types'

const skills = ref<SkillItem[]>([])
const dir = ref('')
/** 技能名 -> 正文。展开时才去取，列表接口不返回正文（它可能上千字）。 */
const contents = ref<Record<string, string>>({})

async function load(): Promise<void> {
  const data = await api.get<{ skills: SkillItem[]; dir: string }>('/skills')
  skills.value = data.skills
  dir.value = data.dir
}

async function toggle(name: string, enabled: boolean): Promise<void> {
  await api.patch(`/skills/${name}`, { enabled })
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

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>技能</h1>
      <span class="hint">
        正文放在 <code class="mono">{{ dir }}/&lt;技能名&gt;/SKILL.md</code>；模型平时只看到名称与适用场景，判断需要时才用 read_skill 取全文
      </span>
    </Teleport>

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

      <el-table-column label="启用" width="80" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row.name, row.enabled)" />
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!skills.length" description="还没有技能：在 skills/ 下建个目录、放个 SKILL.md" />
  </div>
</template>

<style scoped>
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
