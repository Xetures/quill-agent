<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { api } from '../api/client'
import type { ModelConfig, RemoteModel } from '../api/types'
import { loadOptions } from '../stores/session'
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
const saving = ref(false)
const syncing = ref(false)

/** 「获取模型列表」拉回来的候选：模型名 + 尽量拿到的上下文窗口。 */
const candidates = ref<RemoteModel[]>([])

/** 本地模型规格快照能不能用；没同步过时界面上会提示。 */
const catalogReady = ref(false)

/**
 * 用户手动改过窗口大小的模型。
 *
 * 自动填充（按「接口 → 快照」的顺序）只对没被改过的模型生效 —— 否则用户把一个输入框
 * 清空之后，下一次填充又会把它填回来，看起来就像「改了没用」。
 */
const touched = reactive(new Set<string>())

// 新建 / 编辑共用一个弹窗：字段完全一致，拆成两个只会让以后改字段时漏掉一边
const dialogOpen = ref(false)
/** 正在编辑的连接 id；空串表示新建。 */
const editingId = ref('')

const form = ref({
  name: '',
  base_url: '',
  api_key: '',
  protocol: 'openai',
  models: [] as string[],
  /** 模型名 -> 窗口大小；没有这个键就是「不知道」。 */
  context_windows: {} as Record<string, number>,
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
    const result = await api.post<{
      ok: boolean
      models: RemoteModel[]
      detail: string
      catalog_ready: boolean
    }>('/models/test', connectionPayload())

    if (!result.ok) {
      ElMessage.error(result.detail || '获取失败')
      return
    }

    candidates.value = result.models
    catalogReady.value = result.catalog_ready
    fillMissing()

    const suggested = result.models.filter((item) => item.context_window).length
    if (!result.models.length) {
      ElMessage.warning('接口没有返回模型，请手动输入')
    } else if (suggested) {
      ElMessage.success(`拉到 ${result.models.length} 个模型，其中 ${suggested} 个带上下文窗口`)
    } else {
      ElMessage.warning('拉到模型了，但没拿到上下文窗口 —— 可以同步模型库，或自己填')
    }
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    fetching.value = false
  }
}

/**
 * 同步本地模型规格快照（模型名 -> 窗口大小）。
 *
 * 绝大多数官方端点问不出窗口大小，这一层是从公共模型库（默认 models.dev）拉的一份
 * 快照，所以必须由用户显式点一次 —— 不在后台偷偷访问外部网络。拉不到也不影响别的功能。
 */
async function syncCatalog(): Promise<void> {
  syncing.value = true

  try {
    const result = await api.post<{ ok: boolean; message: string; count: number }>(
      '/model-catalog/refresh',
    )

    if (!result.ok) {
      ElMessage.error(result.message || '同步失败')
      return
    }

    catalogReady.value = true
    ElMessage.success(result.message)

    // 顺序是「接口 → 快照」：快照刚更新过，重跑一次拉取才能把新值填进来
    if (form.value.base_url) await fetchModels()
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    syncing.value = false
  }
}

/** 按「接口 → 快照」的顺序，给还没填、且用户没改过的模型补上窗口。 */
function fillMissing(): void {
  for (const name of form.value.models) {
    if (touched.has(name) || form.value.context_windows[name]) continue

    const suggestion = candidates.value.find((item) => item.name === name)?.context_window
    if (suggestion) form.value.context_windows[name] = suggestion
  }
}

/** 还没有窗口线索、也没被用户改过的模型名。 */
function unknownNames(): string[] {
  return form.value.models.filter((name) => {
    if (!name || touched.has(name) || form.value.context_windows[name]) return false
    return !candidates.value.find((item) => item.name === name)?.context_window
  })
}

/**
 * 用本地模型库补一批窗口。
 *
 * 这条路径**不依赖端点**：用户从下拉里手选一个模型、或者只是打开一条已有的连接时，
 * 端点可能根本没被拉过，本地快照仍然能给出窗口。所以除了拉列表之后，打开弹窗和
 * 模型列表变化时也会跑一次。
 */
async function suggestWindows(): Promise<void> {
  const names = unknownNames()
  if (!names.length) return

  try {
    const result = await api.post<{ windows: Record<string, number> }>('/model-catalog/lookup', {
      names,
    })

    for (const [name, tokens] of Object.entries(result.windows)) {
      // 更新已有的候选而不是再推一条：同名候选重复出现会让下拉里多出一项
      const existing = candidates.value.find((item) => item.name === name)
      if (existing) {
        existing.context_window = tokens
        existing.source = 'catalog'
      } else {
        candidates.value.push({ name, context_window: tokens, source: 'catalog' })
      }
    }

    fillMissing()
  } catch {
    // 查不到就算了：这只是「顺手自动填一下」，不该因为它弹个错误打断用户填表
  }
}

// 模型列表一变（从下拉里新选一个、或手输后回车）就用快照补一次
watch(
  () => form.value.models.join('\n'),
  () => void suggestWindows(),
)

/** 用户改窗口：写进去（清空则删掉这个键），并标记为「手动」，不再被自动填充覆盖。 */
function setWindow(name: string, value: number | null | undefined): void {
  touched.add(name)

  if (typeof value === 'number' && value > 0) form.value.context_windows[name] = value
  else delete form.value.context_windows[name]
}

/**
 * 输入框右侧的来源标注。
 *
 * 让用户知道这个数是从哪来的，才好判断要不要信 —— 接口报的可以直接用，
 * 模型库查来的可能过时，手填的就是他自己定的。
 */
function windowSource(name: string): string {
  if (touched.has(name)) return '手动'
  if (!form.value.context_windows[name]) return ''

  const source = candidates.value.find((item) => item.name === name)?.source
  if (source === 'endpoint') return '来自接口'
  if (source === 'catalog') return '来自模型库'
  return ''
}

function openCreate(): void {
  editingId.value = ''
  form.value = {
    name: '',
    base_url: '',
    api_key: '',
    protocol: 'openai',
    models: [],
    context_windows: {},
  }
  // 候选列表跟着旧连接走，换连接后不该再留着
  candidates.value = []
  touched.clear()
  dialogOpen.value = true
}

/**
 * 打开编辑弹窗。
 *
 * 只收 id、按 id 从本地列表里取回整条记录 —— el-table 插槽给的 row 是宽泛的
 * DefaultRow，把整个对象传给要求 ModelConfig 的函数过不了类型检查（见 README 7.12）。
 */
function openEdit(id: string): void {
  const config = configs.value.find((item) => item.id === id)
  if (!config) return

  editingId.value = id
  form.value = {
    name: config.name,
    base_url: config.base_url,
    api_key: config.api_key,
    protocol: config.protocol,
    // 拷一份再改：直接绑原对象的话，取消编辑也会改动列表里的数据
    models: [...config.models],
    context_windows: { ...config.context_windows },
  }
  // 候选下拉先灌上这条连接已有的模型名，打开就能直接增删。
  // 已有的窗口是之前存下来的，不带来源标注（那是「上一次」的结论，不是这次拉到的）
  candidates.value = config.models.map((name) => ({
    name,
    context_window: config.context_windows[name] ?? 0,
    source: '' as const,
  }))
  touched.clear()
  dialogOpen.value = true

  // 已有的窗口里可能有的当初就没填（那时还没接模型库），顺手补一次。
  // 不依赖 watch：连着编辑同一条连接时模型名拼出来的键没变，watch 不会触发
  void suggestWindows()
}

const canSubmit = computed(() => Boolean(form.value.name.trim() && form.value.models.length))

async function submit(): Promise<void> {
  if (!canSubmit.value) return

  // 下拉支持手动输入（allow-create），这里统一 trim 去空
  const models = form.value.models.map((item) => item.trim()).filter(Boolean)

  const payload = {
    name: form.value.name.trim(),
    base_url: form.value.base_url.trim(),
    api_key: form.value.api_key.trim(),
    protocol: form.value.protocol,
    models,
    // 只提交还在列表里的模型：删掉某个模型后，别让它的窗口变成孤儿条目留在配置里
    context_windows: Object.fromEntries(
      models
        .filter((name) => form.value.context_windows[name] > 0)
        .map((name) => [name, form.value.context_windows[name]]),
    ),
  }

  saving.value = true
  try {
    if (editingId.value) {
      await api.put(`/models/${editingId.value}`, payload)
      ElMessage.success('已保存')
    } else {
      await api.post('/models', payload)
      ElMessage.success('已添加')
    }
    dialogOpen.value = false
    await load()
    // 任务页的模型选择器和用量仪表盘读的是 session.models 那份全局选项，
    // 这里不刷新的话，刚填的上下文窗口在任务页上不会生效 —— 看起来就像
    // 「窗口没连到数据」
    await loadOptions()
  } catch (exc) {
    // Key 格式之类的校验文案由后端给，原样弹出，不在前端猜
    ElMessage.error(errorText(exc))
  } finally {
    saving.value = false
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
  // 同上：删掉的连接可能正被任务页选着，得让它跟着更新
  await loadOptions()
  ElMessage.success('已删除')
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

    <!-- 列表上方的操作行：左边计数，右边新建入口。
         原先常驻的表单收进弹窗后，页面只剩「列表 + 一个按钮」 -->
    <div class="bar">
      <span class="muted count">共 {{ configs.length }} 条连接</span>
      <el-button size="small" type="primary" :icon="Plus" class="new-btn" @click="openCreate">
        新建连接
      </el-button>
    </div>

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

      <el-table-column label="模型">
        <template #default="{ row }">
          <el-tag v-for="name in row.models" :key="name" size="small" class="chip">
            {{ name }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="130" align="center">
        <template #default="{ row }">
          <!-- 参数只传 id：row 的字段类型是宽泛的 DefaultRow，取回整条记录在函数里做 -->
          <el-button size="small" text type="primary" @click="openEdit(row.id)">编辑</el-button>
          <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
            删除
          </el-button>
        </template>
      </el-table-column>

      <template #empty>
        <el-empty description="还没有模型配置；点右上角「新建连接」创建" :image-size="60" />
      </template>
    </el-table>

    <!-- 新建 / 编辑共用这一个弹窗：编辑只是带着初值打开同一张表单 -->
    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑连接' : '新建连接'" width="560px">
      <el-form :model="form" label-width="96px" size="default" @submit.prevent>
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
              <el-button size="small" :loading="syncing" @click="syncCatalog">同步模型库</el-button>
              <span class="muted bar-hint">从下拉里选，也可直接输入后回车；可多选</span>
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
              <el-option
                v-for="item in candidates"
                :key="item.name"
                :label="item.name"
                :value="item.name"
              />
            </el-select>

            <p class="note muted">
              「同步模型库」从 models.dev 拉一份「模型名 → 上下文窗口」的快照存到本地，
              只在点它时才访问外部网络。官方端点的
              <code>/models</code> 大多不返回窗口大小，所以这一层通常是拿到窗口的主要途径。
            </p>
          </div>
        </el-form-item>

        <el-form-item label="上下文窗口">
          <div class="windows">
            <p v-if="!form.models.length" class="note muted">
              先在上面选好模型，这里会按模型逐个列出来
            </p>

            <div v-for="name in form.models" :key="name" class="win-row">
              <span class="win-name mono">{{ name }}</span>
              <el-input-number
                :model-value="form.context_windows[name]"
                :min="1"
                :step="1024"
                :controls="false"
                placeholder="未知"
                class="win-input"
                @update:model-value="(value) => setWindow(name, value)"
              />
              <span class="win-src muted">{{ windowSource(name) }}</span>
            </div>

            <p class="note muted">
              tokens。<strong>留空表示不知道</strong>，任务页的用量仪表盘会显示「—」——
              填一个猜的值比留空更糟，它会让人以为上下文还有空间。
              <template v-if="!catalogReady">
                还没同步过模型库，点上面的「同步模型库」可以自动填一批。
              </template>
            </p>
          </div>
        </el-form-item>

        <el-form-item label="连通测试">
          <el-button :loading="testing" :disabled="!form.base_url" @click="testConnection">
            测试连接
          </el-button>
          <span class="muted conn-hint">走 GET /models，不消耗 token</span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">取消</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="saving"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ editingId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 列表上方的操作行：计数在左、新建按钮贴右 */
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.bar .count {
  font-size: 12px;
}

.new-btn {
  margin-left: auto;
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

.bar-hint {
  font-size: 12px;
}

/* 字段下方的说明文字：比正文小一号、留一点上边距，别和控件挤在一起 */
.note {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
}

.note code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--bg-soft);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
}

/* ---------- 每个模型一个窗口输入框 ---------- */

.windows {
  width: 100%;
}

.win-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

/* 模型名可能很长（尤其是中转站的写法），让它自己缩，输入框保持固定宽度 */
.win-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.win-input {
  flex-shrink: 0;
  width: 140px;
}

/* 来源标注占固定宽度：没有值时也占位，输入框不会因为标注出现 / 消失而左右跳 */
.win-src {
  flex-shrink: 0;
  width: 64px;
  font-size: 12px;
}

.conn-hint {
  margin-left: 10px;
  font-size: 12px;
}
</style>
