<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api/client'
import type { ModelConfig } from '../api/types'
import { errorText } from '../utils/error'

const configs = ref<ModelConfig[]>([])
const testing = ref(false)

const form = ref({ name: '', base_url: '', api_key: '', models: '' })

async function load(): Promise<void> {
  const data = await api.get<{ configs: ModelConfig[] }>('/models')
  configs.value = data.configs
}

/** 连通测试：走 GET /models，不消耗 token。 */
async function testConnection(): Promise<void> {
  testing.value = true

  try {
    const result = await api.post<{ ok: boolean; models: string[]; detail: string }>(
      '/models/test',
      { base_url: form.value.base_url, api_key: form.value.api_key, protocol: 'openai' },
    )

    if (result.ok) {
      ElMessage.success(`连接成功，拉到 ${result.models.length} 个模型`)
      // 顺手把前几个填进输入框，省得用户自己敲
      if (!form.value.models) form.value.models = result.models.slice(0, 3).join(', ')
    } else {
      // detail 是原始错误，界面平时不展示；排查时它最有用
      ElMessage.error(result.detail || '连接失败')
    }
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    testing.value = false
  }
}

async function add(): Promise<void> {
  try {
    await api.post('/models', {
      name: form.value.name.trim(),
      base_url: form.value.base_url.trim(),
      api_key: form.value.api_key.trim(),
      protocol: 'openai',
      // 中英文逗号都认，手输时不用讲究
      models: form.value.models
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
    })

    form.value = { name: '', base_url: '', api_key: '', models: '' }
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

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>模型</h1>
      <span class="hint">一条连接可以带多个模型名，同一套凭证只配一次</span>
    </Teleport>

    <el-card shadow="never" class="form-card">
      <el-form :model="form" label-width="80px" size="small">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如 DeepSeek 官方" />
        </el-form-item>

        <el-form-item label="接口地址">
          <el-input v-model="form.base_url" placeholder="例如 https://api.deepseek.com" />
        </el-form-item>

        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password />
        </el-form-item>

        <el-form-item label="模型名">
          <el-input
            v-model="form.models"
            placeholder="逗号分隔，例如 deepseek-chat, deepseek-reasoner"
          />
        </el-form-item>

        <el-form-item>
          <el-button :loading="testing" :disabled="!form.base_url" @click="testConnection">
            连通测试
          </el-button>
          <el-button type="primary" :disabled="!form.name || !form.models.trim()" @click="add">
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

      <el-table-column label="协议" width="110">
        <template #default="{ row }">
          {{ row.protocol === 'openai' ? 'OpenAI 协议' : 'Anthropic 协议' }}
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
</style>
