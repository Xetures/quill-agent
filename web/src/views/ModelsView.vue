<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { ModelConfig } from '../api/types'
import { errorText } from '../utils/error'

/**
 * 协议枚举 -> 展示名。
 *
 * 用两家官方对自家接口的正式叫法（后端 `Protocol.label` 是同一份口径）：
 * OpenAI 的对话接口叫 Chat Completions API，Anthropic 的叫 Messages API。
 * 「OpenAI 协议」并不是标准叫法。
 */
const PROTOCOL_LABELS: Record<string, string> = {
  openai: 'OpenAI Chat Completions',
  anthropic: 'Anthropic Messages',
}

const PROTOCOLS = Object.entries(PROTOCOL_LABELS).map(([value, label]) => ({ value, label }))

const configs = ref<ModelConfig[]>([])
const testing = ref(false)
const fetching = ref(false)

/** 「获取模型列表」拉回来的候选模型名，给下方多选下拉当选项。 */
const candidates = ref<string[]>([])

const form = ref({
  name: '',
  base_url: '',
  api_key: '',
  protocol: 'openai',
  context_window: 128000,
  models: [] as string[],
})

async function load(): Promise<void> {
  const data = await api.get<{ configs: ModelConfig[] }>('/models')
  configs.value = data.configs
}

/** 连通测试 / 拉列表共用同一份请求体。 */
function connectionPayload(): { base_url: string; api_key: string; protocol: string } {
  return {
    base_url: form.value.base_url.trim(),
    api_key: form.value.api_key.trim(),
    protocol: form.value.protocol,
  }
}

/** 连通测试：走 GET /models，不消耗 token。只报「通不通」。 */
async function testConnection(): Promise<void> {
  testing.value = true

  try {
    const result = await api.post<{ ok: boolean; detail: string }>(
      '/models/test',
      connectionPayload(),
    )
    if (result.ok) ElMessage.success('连接成功')
    else ElMessage.error(result.detail || '连接失败')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    testing.value = false
  }
}

/**
 * 获取模型列表：同一个端点，但把拉到的模型名填进下拉当选项。
 *
 * 单独做一个按钮，是因为手敲模型名太容易错 —— 差一个字符就连不上，
 * 而且报错信息通常指不到「名字拼错了」这一点上。
 */
async function fetchModels(): Promise<void> {
  fetching.value = true

  try {
    const result = await api.post<{ ok: boolean; models: string[]; detail: string }>(
      '/models/test',
      connectionPayload(),
    )

    if (!result.ok) {
      ElMessage.error(result.detail || '获取失败')
      return
    }

    candidates.value = result.models
    if (result.models.length) ElMessage.success(`拉到 ${result.models.length} 个模型，从下拉里选`)
    else ElMessage.warning('接口没有返回模型，请手动输入')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    fetching.value = false
  }
}

function resetForm(): void {
  form.value = {
    name: '',
    base_url: '',
    api_key: '',
    protocol: 'openai',
    context_window: 128000,
    models: [],
  }
  // 候选列表跟着旧连接走，换连接后不该再留着
  candidates.value = []
}

async function add(): Promise<void> {
  try {
    await api.post('/models', {
      name: form.value.name.trim(),
      base_url: form.value.base_url.trim(),
      api_key: form.value.api_key.trim(),
      protocol: form.value.protocol,
      context_window: form.value.context_window,
      // 下拉支持手动输入（allow-create），这里统一 trim 去空
      models: form.value.models.map((item) => item.trim()).filter(Boolean),
    })

    resetForm()
    await load()
    ElMessage.success('已添加')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
}

// 参数按字段拆开：el-table 插槽里的 row 是宽泛的 DefaultRow，
// 直接传整个对象过不了类型检查
async function remove(id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除连接「${name}」？该连接下的模型会一起消失。`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  await api.del(`/models/${id}`)
  await load()
  ElMessage.success('已删除')
}

/** 上下文窗口展示：按 k 取整，够短也好读。 */
function formatWindow(tokens: number): string {
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : String(tokens)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>API 设置</h1>
      <span class="hint">模型服务的连接配置；一条连接可以带多个模型名，同一套凭证只配一次</span>
    </Teleport>

    <el-card shadow="never" class="form-card">
      <el-form :model="form" label-width="96px" size="small">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如 DeepSeek 官方" />
        </el-form-item>

        <el-form-item label="协议">
          <el-select v-model="form.protocol" class="protocol-select">
            <el-option
              v-for="item in PROTOCOLS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="接口地址">
          <el-input v-model="form.base_url" placeholder="例如 https://api.deepseek.com" />
        </el-form-item>

        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password />
        </el-form-item>

        <el-form-item label="模型名">
          <div class="model-block">
            <div class="model-bar">
              <el-button
                size="small"
                :loading="fetching"
                :disabled="!form.base_url"
                @click="fetchModels"
              >
                获取模型列表
              </el-button>
              <span class="muted">从下拉里选，也可直接输入后回车；可多选</span>
            </div>

            <!-- filterable + allow-create：既能从拉到的列表里选，也能手动输入
                 （有些中转站没有 /models 端点，或列表里没有目标模型） -->
            <el-select
              v-model="form.models"
              multiple
              filterable
              allow-create
              default-first-option
              collapse-tags
              collapse-tags-tooltip
              placeholder="例如 deepseek-chat"
              class="model-select"
            >
              <el-option v-for="name in candidates" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
        </el-form-item>

        <el-form-item label="上下文窗口">
          <div class="ctx-block">
            <el-input-number
              v-model="form.context_window"
              :min="1"
              :step="1024"
              controls-position="right"
              class="ctx-input"
            />
            <span class="muted">tokens，任务页的用量仪表盘按它算占比</span>
          </div>
        </el-form-item>

        <el-form-item>
          <el-button :loading="testing" :disabled="!form.base_url" @click="testConnection">
            连通测试
          </el-button>
          <el-button type="primary" :disabled="!form.name || !form.models.length" @click="add">
            添加
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-table :data="configs" size="small" stripe>
      <el-table-column prop="name" label="名称" width="160" />

      <el-table-column label="接口地址">
        <template #default="{ row }">
          <span class="mono">{{ row.base_url || '（默认地址）' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="协议" width="180">
        <template #default="{ row }">
          {{ PROTOCOL_LABELS[row.protocol] ?? row.protocol }}
        </template>
      </el-table-column>

      <el-table-column label="上下文" width="90" align="right">
        <template #default="{ row }">
          <span class="mono">{{ formatWindow(row.context_window) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="模型">
        <template #default="{ row }">
          <el-tag v-for="name in row.models" :key="name" size="small" class="chip">
            {{ name }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!configs.length" description="还没有模型配置，先在上面添加一条" />
  </div>
</template>

<style scoped>
.form-card {
  margin-bottom: 16px;
}

.chip {
  margin-right: 4px;
}

.protocol-select {
  width: 240px;
}

/* 模型名字段：一整块（按钮行 + 多选下拉），占满表单宽度 */
.model-block {
  width: 100%;
}

.model-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.model-select {
  width: 100%;
}

.ctx-block {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ctx-input {
  width: 160px;
}
</style>
