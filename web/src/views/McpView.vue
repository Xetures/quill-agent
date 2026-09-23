<script setup lang="ts">
import { Plus, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import { useTableHeight } from '../composables/useTableHeight'
import { errorText } from '../utils/error'

const { t } = useI18n()

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

/** 表格容器：量它的高度交给 el-table，让表体自己滚（见 useTableHeight） */
const tableBox = ref<HTMLElement | null>(null)
const tableHeight = useTableHeight(tableBox)
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
    // `?? []`：字段缺失也要能打开页面与弹窗（下面到处在 `.length` / 迭代它）
    servers.value = data.servers ?? []
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
      ElMessage.success(t('mcp.updated'))
    } else {
      await api.post('/mcp/servers', payload())
      ElMessage.success(t('mcp.added'))
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
      t('mcp.deleteConfirm', { name: row.name, count: row.tools.length }),
      t('mcp.deleteTitle'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') },
    )
  } catch {
    return
  }

  try {
    await api.del(`/mcp/servers/${encodeURIComponent(row.id)}`)
    ElMessage.success(t('common.deleted'))
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
  ElMessage.success(t('mcp.reconnected'))
}

onMounted(load)
</script>

<template>
  <div class="mcp">
    <div class="head">
      <div>
        <h2>{{ t('mcp.title') }}</h2>
        <p class="hint">
          <span v-html="t('mcp.warning')" />
        </p>
      </div>
      <div class="head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="reload">{{ t('mcp.reconnect') }}</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">{{ t('mcp.add') }}</el-button>
      </div>
    </div>

    <!-- 表格自己滚：表头固定、只有表体在滚（高度由 useTableHeight 量出） -->
    <div ref="tableBox" class="table-box">
      <el-table
        :data="servers"
        :height="tableHeight"
        v-loading="loading"
        :empty-text="t('mcp.empty')"
      >
      <el-table-column :label="t('mcp.columnName')" min-width="140">
        <template #default="{ row }">
          <div class="name">
            <span>{{ row.name }}</span>
            <el-tag v-if="!row.enabled" size="small" type="info" effect="plain">{{ t('mcp.disabled') }}</el-tag>
          </div>
          <div v-if="row.description" class="muted desc">{{ row.description }}</div>
        </template>
      </el-table-column>

      <el-table-column :label="t('mcp.columnTransport')" width="150">
        <template #default="{ row }">
          <div class="mono transport">{{ row.transport }}</div>
          <div class="muted mono cmd" :title="[row.command, ...row.args].join(' ')">
            {{ row.transport === 'stdio' ? [row.command, ...row.args].join(' ') : row.url }}
          </div>
        </template>
      </el-table-column>

      <el-table-column :label="t('mcp.columnStatus')" min-width="200">
        <template #default="{ row }">
          <template v-if="!row.enabled">
            <span class="muted">{{ t('mcp.disabled') }}</span>
          </template>
          <template v-else-if="row.connected">
            <el-tag size="small" type="success" effect="plain">
              {{ t('mcp.connected', { count: row.tools.length }) }}
            </el-tag>
          </template>
          <template v-else>
            <el-tooltip :content="row.error || t('mcp.notConnected')" placement="top">
              <el-tag size="small" type="danger" effect="plain">{{ t('mcp.connectFailed') }}</el-tag>
            </el-tooltip>
            <div class="muted error-text">{{ row.error || t('mcp.notConnected') }}</div>
          </template>
        </template>
      </el-table-column>

      <el-table-column :label="t('mcp.columnActions')" width="210" align="right">
        <template #default="{ row }">
          <el-button size="small" text @click="openEdit(row as McpServerRow)">{{ t('common.edit') }}</el-button>
          <el-button size="small" text @click="toggle(row as McpServerRow)">
            {{ row.enabled ? t('mcp.disable') : t('mcp.enable') }}
          </el-button>
          <el-button size="small" text type="danger" @click="remove(row as McpServerRow)">
            {{ t('common.delete') }}
          </el-button>
        </template>
      </el-table-column>
      </el-table>
    </div>

    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialog"
      :title="editingId ? t('mcp.editTitle') : t('mcp.addTitle')"
      width="640px"
      append-to-body
    >
      <el-form label-width="92px" label-position="left">
        <el-form-item :label="t('mcp.form.name')">
          <el-input v-model="form.name" :placeholder="t('mcp.form.namePlaceholder')" />
          <div class="hint">
            <span v-html="t('mcp.form.nameHint')" />
          </div>
        </el-form-item>
        <el-form-item :label="t('mcp.form.description')">
          <el-input v-model="form.description" :placeholder="t('mcp.form.descriptionPlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('mcp.form.transport')">
          <el-radio-group v-model="form.transport">
            <el-radio-button value="stdio">{{ t('mcp.form.transportStdio') }}</el-radio-button>
            <el-radio-button value="http">{{ t('mcp.form.transportHttp') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="isStdio">
          <el-form-item :label="t('mcp.form.command')">
            <el-input v-model="form.command" :placeholder="t('mcp.form.commandPlaceholder')" />
          </el-form-item>
          <el-form-item :label="t('mcp.form.args')">
            <el-input
              v-model="form.argsText"
              type="textarea"
              :rows="3"
              :placeholder="t('mcp.form.argsPlaceholder')"
            />
          </el-form-item>
          <el-form-item :label="t('mcp.form.env')">
            <el-input
              v-model="form.envText"
              type="textarea"
              :rows="2"
              :placeholder="t('mcp.form.envPlaceholder')"
            />
          </el-form-item>
        </template>

        <el-form-item v-else :label="t('mcp.form.url')">
          <el-input v-model="form.url" :placeholder="t('mcp.form.urlPlaceholder')" />
        </el-form-item>

        <el-form-item :label="t('mcp.form.timeout')">
          <el-input-number v-model="form.timeout" :min="5" :max="300" :step="5" />
          <span class="hint inline">{{ t('mcp.form.timeoutHint') }}</span>
        </el-form-item>

        <!-- 保存前先试一次：能不能起、能不能列出工具 -->
        <el-form-item label=" ">
          <el-button :loading="testing" @click="testConnection">{{ t('mcp.form.test') }}</el-button>
          <span v-if="probe" class="probe">
            <el-tag v-if="probe.ok" size="small" type="success" effect="plain">
              {{ t('mcp.form.testOk', { count: probe.tools.length }) }}
            </el-tag>
            <el-tag v-else size="small" type="danger" effect="plain">{{ t('mcp.form.testFailed') }}</el-tag>
          </span>
        </el-form-item>
      </el-form>

      <div v-if="probe" class="probe-box">
        <template v-if="probe.ok">
          <div class="muted">{{ t('mcp.form.provides') }}</div>
          <div v-for="tool in probe.tools" :key="tool.name" class="mono probe-line">
            {{ tool.name }} <span class="muted">← {{ tool.remote }}</span>
          </div>
        </template>
        <div v-else class="probe-error">{{ probe.error }}</div>
      </div>

      <template #footer>
        <el-button @click="dialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!form.name.trim()" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 整页不滚，表格自己滚（见 .table-box）—— 表头与操作行钉在原处，只有表体在滚 */
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.table-box {
  flex: 1;
  min-height: 0;
}

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
  font-size: var(--fs-lg);
}

.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.hint {
  margin: 4px 0 0;
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
  margin-top: 2px;
}

.cmd,
.transport {
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
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
