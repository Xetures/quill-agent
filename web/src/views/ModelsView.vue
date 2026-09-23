<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import type { ModelConfig, ProviderOption, ProtocolOption, RemoteModel } from '../api/types'

const { t } = useI18n()
import { useTableHeight } from '../composables/useTableHeight'
import { loadOptions } from '../stores/session'
import { errorText } from '../utils/error'

/**
 * 可选协议：由后端下发（`GET /protocols`），前端不再硬编码一份。
 *
 * 展示名用各家对自家接口的正式叫法：OpenAI 的对话接口叫 Chat Completions API，
 * Anthropic 的叫 Messages API，「OpenAI 协议」并不是标准叫法；Ollama 走的是它
 * 自带的 OpenAI 兼容层（`/v1/chat/completions`）。
 */
const protocols = ref<ProtocolOption[]>([])

/**
 * 官方服务商清单（`GET /providers`）。
 *
 * 和模型规格快照**同一次同步**写出来的两个文件，所以它空着就表示「还没同步过」——
 * 界面据此提示用户去点一次「同步模型库」，不必为它单设一个按钮。
 */
const providers = ref<ProviderOption[]>([])

/** 协议 value -> 展示名；表格与下拉共用（后端加协议时这里自动跟上）。 */
const protocolLabels = computed<Record<string, string>>(() =>
  Object.fromEntries(protocols.value.map((item) => [item.value, item.label])),
)

/** 某个协议的默认地址；没配默认值（远端服务）就给空串。 */
function defaultBaseUrl(protocol: string): string {
  return protocols.value.find((item) => item.value === protocol)?.default_base_url ?? ''
}

const configs = ref<ModelConfig[]>([])

/** 表格容器：量它的高度交给 el-table，让表体自己滚（见 useTableHeight） */
const tableBox = ref<HTMLElement | null>(null)
const tableHeight = useTableHeight(tableBox)
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

/**
 * 新建 / 编辑共用同一张表单。表单顶部的「服务商」下拉是**可选**的捷径：
 * 选一家就把名字、地址、协议带出来，不选就全部手填 —— 两种走法最后都是
 * 同一条连接、同一个提交。拆成两个入口的方案试过，代价是多一个按钮、
 * 却只有一层预填的差别，不如把选择权交给表单自己。
 */
const providerId = ref('')

const form = ref({
  name: '',
  base_url: '',
  api_key: '',
  protocol: 'openai',
  models: [] as string[],
  /** 模型名 -> 窗口大小；没有这个键就是「不知道」。 */
  context_windows: {} as Record<string, number>,
})

/** 当前所选协议的 Key 提示；直接拿来做输入框的 placeholder。 */
const apiKeyHint = computed(
  () => protocols.value.find((item) => item.value === form.value.protocol)?.hint ?? '',
)

async function load(): Promise<void> {
  const [data, options, catalog] = await Promise.all([
    api.get<{ configs: ModelConfig[] }>('/models'),
    api.get<{ protocols: ProtocolOption[] }>('/protocols'),
    api.get<{ providers: ProviderOption[] }>('/providers'),
  ])
  configs.value = data.configs
  protocols.value = options.protocols
  // `?? []`：清单拿不到时也要能打开弹窗 —— 下面到处在 `.length` 它，
  // 少了这个兜底，一个字段缺失就会让整个表单渲染失败（表现是「点了没反应」）
  providers.value = catalog.providers ?? []
}

// 换协议时顺手把地址填好 —— 但只在「地址为空」或「还留着上一个协议的默认地址」时动手，
// 用户自己填过的地址绝不覆盖。场景很具体：选了 Ollama，没人愿意去背
// http://localhost:11434/v1 这个地址
watch(
  () => form.value.protocol,
  (current, previous) => {
    const shown = form.value.base_url.trim()
    if (!shown || shown === defaultBaseUrl(previous)) {
      form.value.base_url = defaultBaseUrl(current)
    }
  },
)

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
    if (result.ok) ElMessage.success(t('models.ok'))
    else ElMessage.error(result.detail || t('models.connectFailed'))
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
      ElMessage.error(result.detail || t('models.fetchFailed'))
      return
    }

    candidates.value = result.models
    catalogReady.value = result.catalog_ready
    fillMissing()

    const suggested = result.models.filter((item) => item.context_window).length
    if (!result.models.length) {
      ElMessage.warning(t('models.fetchEmpty'))
    } else if (suggested) {
      ElMessage.success(t('models.fetched', { count: result.models.length, suggested }))
    } else {
      ElMessage.warning(t('models.fetchedNoWindow'))
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
      ElMessage.error(result.message || t('models.syncFailed'))
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
  // **先把候选里已有的窗口填上，再去查快照** —— 这就是「接口 → 快照」的顺序。
  //
  // 用户从下拉里选中模型时，candidates（「{{ t('models.form.fetchModels') }}」拉回来的）早就备好了，
  // 里面往往带着接口报的窗口。但原先只有「确实要查快照」时才会走到 fillMissing()：
  // 选中的模型若在拉列表时就带着窗口，unknownNames() 会把它排除掉、这里直接
  // return —— 那一格就空着，看起来像「选了列表却不自动填」。
  // 这一行放在最前面，选中的瞬间就先吃掉候选里现成的值。
  fillMissing()

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
  if (touched.has(name)) return t('models.source.manual')
  if (!form.value.context_windows[name]) return ''

  const source = candidates.value.find((item) => item.name === name)?.source
  if (source === 'endpoint') return t('models.source.endpoint')
  if (source === 'catalog') return t('models.source.catalog')
  return ''
}

/**
 * 选中一个官方服务商：把它的名字、地址、协议填进表单。
 *
 * 直接覆盖而不是「只在空着时才填」：点服务商这个动作的意思就是「按这家来」，
 * 之前手改的名字或地址在按下去的那一刻就不要了。想全靠自己填，别碰这个下拉。
 */
function pickProvider(id: string): void {
  const provider = providers.value.find((item) => item.id === id)
  if (!provider) return

  form.value.protocol = provider.protocol
  form.value.base_url = provider.base_url
  form.value.name = provider.name
}

/** 打开新建弹窗。服务商下拉总是显示，选不选由用户定（见 providerId）。 */
function openCreate(): void {
  providerId.value = ''
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
 * DefaultRow，把整个对象传给要求 ModelConfig 的函数过不了类型检查（见 README-developer.md 7.12）。
 */
function openEdit(id: string): void {
  const config = configs.value.find((item) => item.id === id)
  if (!config) return

  // 编辑的是既有连接，它自己带着地址与协议；服务商下拉清空 ——
  // 「来自哪家」是创建时的选择，不是连接的属性，这里也不必显示
  providerId.value = ''
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

const dialogTitle = computed(() => (editingId.value ? t('models.form.editTitle') : t('models.form.createTitle')))

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
      ElMessage.success(t('models.saved'))
    } else {
      await api.post('/models', payload)
      ElMessage.success(t('models.added'))
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
    await ElMessageBox.confirm(t('models.deleteConfirm', { name }), t('models.deleteTitle'), {
      type: 'warning',
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
    })
  } catch {
    return
  }

  await api.del(`/models/${id}`)
  await load()
  // 同上：删掉的连接可能正被任务页选着，得让它跟着更新
  await loadOptions()
  ElMessage.success(t('common.deleted'))
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <Teleport to="#page-head-slot">
      <h1>{{ t('models.title') }}</h1>
      <span class="hint">{{ t('models.hint') }}</span>
    </Teleport>

    <!-- 列表上方的操作行：左边计数，右边新建入口。
         原先常驻的表单收进弹窗后，页面只剩「列表 + 一个按钮」 -->
    <div class="bar">
      <span class="muted count">{{ t('models.count', { count: configs.length }) }}</span>
      <!-- 一个入口。表单顶部的「服务商」是可选捷径：选一家就带出名字、地址、协议，
           不选就全部手填 —— 两种走法汇进同一张表单（见 providerId 那段说明） -->
      <el-button size="small" type="primary" :icon="Plus" class="new-btn" @click="openCreate">
        {{ t('models.create') }}
      </el-button>
    </div>

    <!-- 表格自己滚：表头固定、只有表体在滚（高度由 useTableHeight 量出） -->
    <div ref="tableBox" class="table-box">
      <el-table :data="configs" :height="tableHeight" size="small" stripe>
      <el-table-column prop="name" :label="t('models.columnName')" width="160" />

      <el-table-column :label="t('models.columnUrl')">
        <template #default="{ row }">
          <span class="mono">
            {{ row.base_url || defaultBaseUrl(row.protocol) || t('models.defaultUrl') }}
          </span>
        </template>
      </el-table-column>

      <el-table-column :label="t('models.columnProtocol')" width="180">
        <template #default="{ row }">
          {{ protocolLabels[row.protocol] ?? row.protocol }}
        </template>
      </el-table-column>

      <el-table-column :label="t('models.columnModels')">
        <template #default="{ row }">
          <el-tag v-for="name in row.models" :key="name" size="small" class="chip">
            {{ name }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column :label="t('models.columnActions')" width="130" align="center">
        <template #default="{ row }">
          <!-- 参数只传 id：row 的字段类型是宽泛的 DefaultRow，取回整条记录在函数里做 -->
          <el-button size="small" text type="primary" @click="openEdit(row.id)">{{ t('common.edit') }}</el-button>
          <el-button size="small" text type="danger" @click="remove(row.id, row.name)">
            {{ t('common.delete') }}
          </el-button>
        </template>
      </el-table-column>

      <template #empty>
        <el-empty :description="t('models.empty')" :image-size="60" />
      </template>
      </el-table>
    </div>

    <!-- 新建 / 编辑共用这一个弹窗：编辑只是带着初值打开同一张表单 -->
    <!-- append-to-body 必须留着：玻璃板的 backdrop-filter 会改掉弹窗 fixed 的参考系
         （详见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="dialogOpen"
      :title="dialogTitle"
      width="560px"
      append-to-body
    >
      <el-form :model="form" label-width="96px" size="default" @submit.prevent>
        <!-- 可选的捷径：选一家就把名字、地址、协议一起带出来，不想用就空着，下面照旧手填。
             清单为空时给个同步入口 —— 清单和窗口快照是同一份下载数据 -->
        <el-form-item :label="t('models.form.provider')">
          <div class="provider-block">
            <el-select
              v-model="providerId"
              filterable
              clearable
              :placeholder="t('models.form.providerPlaceholder')"
              class="provider-select"
              @change="(value: string) => pickProvider(value)"
            >
              <el-option
                v-for="item in providers"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
            </el-select>
            <p v-if="!providers.length" class="note muted">
              {{ t('models.form.catalogSyncBefore') }}
              <el-button link type="primary" size="small" @click="syncCatalog">
                {{ t('models.form.catalogSyncButton') }}
              </el-button>
              {{ t('models.form.catalogSyncAfter') }}
            </p>
            <p v-else class="note muted">
              {{ t('models.form.providerOptional') }}
            </p>
          </div>
        </el-form-item>

        <el-form-item :label="t('models.form.name')">
          <el-input v-model="form.name" :placeholder="t('models.form.namePlaceholder')" />
        </el-form-item>

        <el-form-item :label="t('models.form.protocol')">
          <el-select v-model="form.protocol" class="protocol-select">
            <el-option
              v-for="item in protocols"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('models.columnUrl')">
          <div class="provider-block">
            <el-input
              v-model="form.base_url"
              :placeholder="defaultBaseUrl(form.protocol) || t('models.form.urlPlaceholder')"
            />
            <!-- 第一方官方（OpenAI、Anthropic…）的快照里就没有地址 —— SDK 自己知道
                 官方地址。这里明说一句，免得用户以为漏填了 -->
            <p v-if="providerId && !form.base_url" class="note muted">
              {{ t('models.form.urlOfficial') }}
            </p>
          </div>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="apiKeyHint"
          />
        </el-form-item>

        <el-form-item :label="t('models.form.modelNames')">
          <div class="model-block">
            <div class="model-bar">
              <!-- 地址为空**不能**禁用：官方服务那几家的地址本来就留空
                   （见上面「接口地址」那格），SDK 会用官方地址去拉 -->
              <el-button size="small" :loading="fetching" @click="fetchModels">
                {{ t('models.form.fetchModels') }}
              </el-button>
              <span class="muted bar-hint">{{ t('models.form.fetchHint') }}</span>
            </div>

            <!-- filterable + allow-create：既能从拉到的列表里选，也能手动输入
                 {{ t('models.form.fetchFallback') }} -->
            <el-select
              v-model="form.models"
              multiple
              filterable
              allow-create
              default-first-option
              collapse-tags
              collapse-tags-tooltip
              :placeholder="t('models.form.modelPlaceholder')"
              class="model-select"
            >
              <el-option
                v-for="item in candidates"
                :key="item.name"
                :label="item.name"
                :value="item.name"
              />
            </el-select>
          </div>
        </el-form-item>

        <el-form-item :label="t('models.form.context')">
          <div class="windows">
            <p v-if="!form.models.length" class="note muted">
              {{ t('models.form.contextPickFirst') }}
            </p>

            <div v-for="name in form.models" :key="name" class="win-row">
              <span class="win-name mono">{{ name }}</span>
              <el-input-number
                :model-value="form.context_windows[name]"
                :min="1"
                :step="1024"
                :controls="false"
                :placeholder="t('models.form.contextUnknown')"
                class="win-input"
                @update:model-value="(value) => setWindow(name, value)"
              />
              <span class="win-src muted">{{ windowSource(name) }}</span>
            </div>

            <p class="note muted">
              <span v-html="t('models.form.contextHint')" />
              <template v-if="!catalogReady">{{ t('models.form.contextNoCatalog') }}</template>
            </p>
          </div>
        </el-form-item>

        <el-form-item :label="t('models.form.test')">
          <!-- 地址为空同样不禁用（同上）：第一方官方走 SDK 的官方地址 -->
          <el-button :loading="testing" @click="testConnection">
            {{ t('models.form.testButton') }}
          </el-button>
          <span class="muted conn-hint">{{ t('models.form.testHint') }}</span>
        </el-form-item>

        <!-- 同步放最后一行：它服务的两样东西（服务商清单、窗口快照）都在上面用得到，
             但都不是「填这条连接」的必经步骤，压轴而不是打头 -->
        <el-form-item :label="t('models.form.syncLabel')">
          <el-button size="small" :loading="syncing" @click="syncCatalog">{{ t('models.form.catalogSyncButton') }}</el-button>
          <span class="muted conn-hint">
            {{ t('models.form.syncHint') }}
          </span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button size="small" @click="dialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="saving"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ editingId ? t('common.save') : t('common.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>

/* 整页不滚，表格自己滚（见 .table-box）：表头固定、只有表体在滚。
 * 外层套 el-scrollbar 那版会让表头跟着滚走，而且鼠标停在表格上滚不动（用户反馈） */
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

/* 列表上方的操作行：计数在左、新建按钮贴右。
 * 和下面列表之间的距离由 .page 的 gap 给，这里不再另加 —— 两个一起会叠成两倍 */
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bar .count {
  font-size: var(--fs-xs);
}

.new-btn {
  margin-left: auto;
}

/* 服务商那一格：下拉 + 下方说明，占满表单宽度（和「模型名」同款结构） */
.provider-block {
  width: 100%;
}

.provider-select {
  width: 100%;
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
  font-size: var(--fs-xs);
}

/* 字段下方的说明文字：比正文小一号、留一点上边距，别和控件挤在一起 */
.note {
  margin: 6px 0 0;
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
}

.conn-hint {
  margin-left: 10px;
  font-size: var(--fs-xs);
}
</style>
