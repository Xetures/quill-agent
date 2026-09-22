<script setup lang="ts">
import { Plus, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '../api/client'
import { errorText } from '../utils/error'

/**
 * MCP 服务器管理。
 *
 * 两条设计上的重点：
 *
 * 1. **保存前能试连。** 用户填的是一行命令加一串参数，光看配置判断不了它能不能跑起来
 *    （命令不存在、包名写错、要联网、要 token）。试连把「保存之后对着空工具列表猜」变成了
 *    「当场看到它能提供什么」。
 * 2. **把要执行的命令原样显示出来。** 加一个 MCP 服务器 = 允许它在这台机器上跑进程，
 *    所以在保存确认里要把命令行摊开给用户看 —— 藏在输入框里等于没告知。
 */
interface McpServerRow {
  id: string
  name: string
  description: string
  transport: 'stdio' | 'http'
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  enabled: boolean
  timeout: number
  /** 以下三个是运行状态，不属于配置（由后端一并返回） */
  connected: boolean
  error: string
  tools: string[]
}

const servers = ref<McpServerRow[]>([])
const loading = ref(false)

const dialog = ref(false)
const editingId = ref('')
const testing = ref(false)
/** 试连结果：undefined 表示还没试过 */
const probe = ref<{ ok: boolean; error: string; tools: { name: string; remote: string }[] } | null>(
  null,
)

/** 参数和环境变量在界面上用文本框写，提交时再拆 —— 比动态增删行省事得多 */
const form = reactive({
  name: '',
  description: '',
  transport: 'stdio' as 'stdio' | 'http',
  command: '',
  argsText: '',
  envText: '',
  url: '',
  timeout: 30,
})

const isStdio = computed(() => form.transport === 'stdio')

/** 提交给后端的形状。 */
function payload(): Record<string, unknown> {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    transport: form.transport,
    command: form.command.trim(),
    // 每行一个参数：命令行里的空格会让人分不清「一个参数里有空格」还是「两个参数」
    args: form.argsText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean),
    env: Object.fromEntries(
      form.envText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const index = line.indexOf('=')
          return index === -1 ? [line, ''] : [line.slice(0, index).trim(), line.slice(index + 1).trim()]
        }),
    ),
    url: form.url.trim(),
    timeout: form.timeout,
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await api.get<{ servers: McpServerRow[] }>('/mcp/servers')
    servers.value = data.servers
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = ''
  Object.assign(form, {
    name: '',
    description: '',
    transport: 'stdio',
    command: '',
    argsText: '',
    envText: '',
    url: '',
    timeout: 30,
  })
  probe.value = null
  dialog.value = true
}

function openEdit(row: McpServerRow): void {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    description: row.description,
    transport: row.transport,
    command: row.command,
    argsText: row.args.join('\n'),
    envText: Object.entries(row.env)
      .map(([key, value]) => `${key}=${value}`)
      .join('\n'),
    url: row.url,
    timeout: row.timeout,
  })
  probe.value = null
  dialog.value = true
}

/** 试连当前表单里的配置（尚未保存）。 */
async function testConnection(): Promise<void> {
  testing.value = true
  probe.value = null
  try {
    probe.value = await api.post('/mcp/test', payload())
  } catch (exc) {
    probe.value = { ok: false, error: errorText(exc), tools: [] }
  } finally {
    testing.value = false
  }
}

async function save(): Promise<void> {
  try {
    if (editingId.value) {
      await api.put(`/mcp/servers/${encodeURIComponent(editingId.value)}`, payload())
      ElMessage.success('已更新')
    } else {
      await api.post('/mcp/servers', payload())
      ElMessage.success('已添加')
    }
  } catch (exc) {
    ElMessage.error(errorText(exc))
    return
  }

  dialog.value = false
  await load()
}

async function toggle(row: McpServerRow): Promise<void> {
  try {
    await api.post(`/mcp/servers/${encodeURIComponent(row.id)}/toggle`, {})
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
  await load()
}

async function remove(row: McpServerRow): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除 MCP 服务器「${row.name}」？它提供的 ${row.tools.length} 个工具会立刻从可用工具里消失。`,
      '删除 MCP 服务器',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await api.del(`/mcp/servers/${encodeURIComponent(row.id)}`)
    ElMessage.success('已删除')
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
  await load()
}

async function reload(): Promise<void> {
  loading.value = true
  try {
    await api.post('/mcp/reload', {})
  } catch (exc) {
    ElMessage.error(errorText(exc))
  }
  await load()
  ElMessage.success('已重新连接')
}

onMounted(load)
</script>

<template>
  <div class="mcp">
    <div class="head">
      <div>
        <h2>MCP 服务器</h2>
        <p class="hint">
          接入外部工具来源。<strong>添加一个服务器 = 允许它在这台机器上执行任意代码</strong>
          —— stdio 会起一个进程，而且它<strong>不在沙箱内</strong>（沙箱管的是模型执行的命令）。
          只添加你信任的服务器。
        </p>
      </div>
      <div class="head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="reload">重新连接</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">添加服务器</el-button>
      </div>
    </div>

    <el-table :data="servers" v-loading="loading" empty-text="还没有配置 MCP 服务器">
      <el-table-column label="名称" min-width="140">
        <template #default="{ row }">
          <div class="name">
            <span>{{ row.name }}</span>
            <el-tag v-if="!row.enabled" size="small" type="info" effect="plain">已停用</el-tag>
          </div>
          <div v-if="row.description" class="muted desc">{{ row.description }}</div>
        </template>
      </el-table-column>

      <el-table-column label="连接方式" width="150">
        <template #default="{ row }">
          <div class="mono transport">{{ row.transport }}</div>
          <div class="muted mono cmd" :title="[row.command, ...row.args].join(' ')">
            {{ row.transport === 'stdio' ? [row.command, ...row.args].join(' ') : row.url }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="状态" min-width="200">
        <template #default="{ row }">
          <template v-if="!row.enabled">
            <span class="muted">已停用</span>
          </template>
          <template v-else-if="row.connected">
            <el-tag size="small" type="success" effect="plain">
              已连接 · {{ row.tools.length }} 个工具
            </el-tag>
          </template>
          <template v-else>
            <el-tooltip :content="row.error || '尚未连接'" placement="top">
              <el-tag size="small" type="danger" effect="plain">连接失败</el-tag>
            </el-tooltip>
            <div class="muted error-text">{{ row.error || '尚未连接' }}</div>
          </template>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="210" align="right">
        <template #default="{ row }">
          <el-button size="small" text @click="openEdit(row as McpServerRow)">编辑</el-button>
          <el-button size="small" text @click="toggle(row as McpServerRow)">
            {{ row.enabled ? '停用' : '启用' }}
          </el-button>
          <el-button size="small" text type="danger" @click="remove(row as McpServerRow)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialog"
      :title="editingId ? '编辑 MCP 服务器' : '添加 MCP 服务器'"
      width="640px"
    >
      <el-form label-width="92px" label-position="left">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如 filesystem" />
          <div class="hint">
            只能是字母、数字、下划线、短横 —— 它会成为工具名的前缀（
            <span class="mono">mcp__名称__工具</span>），而模型 API 对名字有字符集限制。
          </div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" placeholder="可选，帮你想起它是干嘛的" />
        </el-form-item>
        <el-form-item label="连接方式">
          <el-radio-group v-model="form.transport">
            <el-radio-button value="stdio">stdio（本地进程）</el-radio-button>
            <el-radio-button value="http">http（远程）</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="isStdio">
          <el-form-item label="命令">
            <el-input v-model="form.command" placeholder="如 npx 或 uvx" />
          </el-form-item>
          <el-form-item label="参数">
            <el-input
              v-model="form.argsText"
              type="textarea"
              :rows="3"
              placeholder="每行一个，例如：&#10;-y&#10;@modelcontextprotocol/server-filesystem&#10;/tmp"
            />
          </el-form-item>
          <el-form-item label="环境变量">
            <el-input
              v-model="form.envText"
              type="textarea"
              :rows="2"
              placeholder="每行一个 KEY=VALUE，可选"
            />
          </el-form-item>
        </template>

        <el-form-item v-else label="地址">
          <el-input v-model="form.url" placeholder="如 http://127.0.0.1:3000/mcp" />
        </el-form-item>

        <el-form-item label="超时">
          <el-input-number v-model="form.timeout" :min="5" :max="300" :step="5" />
          <span class="hint inline">秒。单次调用的上限 —— 超时后等待会被放弃。</span>
        </el-form-item>

        <!-- 保存前先试一次：能不能起、能不能列出工具 -->
        <el-form-item label=" ">
          <el-button :loading="testing" @click="testConnection">试连接</el-button>
          <span v-if="probe" class="probe">
            <el-tag v-if="probe.ok" size="small" type="success" effect="plain">
              成功 · {{ probe.tools.length }} 个工具
            </el-tag>
            <el-tag v-else size="small" type="danger" effect="plain">失败</el-tag>
          </span>
        </el-form-item>
      </el-form>

      <div v-if="probe" class="probe-box">
        <template v-if="probe.ok">
          <div class="muted">它提供这些工具：</div>
          <div v-for="tool in probe.tools" :key="tool.name" class="mono probe-line">
            {{ tool.name }} <span class="muted">← {{ tool.remote }}</span>
          </div>
        </template>
        <div v-else class="probe-error">{{ probe.error }}</div>
      </div>

      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="!form.name.trim()" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mcp {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.head h2 {
  margin: 0 0 6px;
  font-size: 15px;
}

.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-soft);
}

.hint.inline {
  display: inline;
  margin-left: 8px;
}

.name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.desc,
.cmd,
.error-text {
  font-size: 12px;
  margin-top: 2px;
}

.cmd,
.transport {
  font-size: 12px;
}

/* 命令行会很长，截断并靠 title 显示全文 */
.cmd {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}

.error-text {
  color: var(--el-color-danger);
  word-break: break-all;
}

.probe {
  margin-left: 10px;
}

.probe-box {
  border-top: 1px solid var(--border);
  padding-top: 10px;
  font-size: 12px;
  max-height: 180px;
  overflow-y: auto;
}

.probe-line {
  margin-top: 4px;
}

.probe-error {
  color: var(--el-color-danger);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
