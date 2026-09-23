<script setup lang="ts">
import { Lock, Unlock } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import { errorText } from '../utils/error'

/** 后端 `/api/sandbox` 的返回，字段与 `sandbox.status()` 一一对应。 */
interface SandboxModeOption {
  value: string
  label: string
  hint: string
}

interface SandboxStatus {
  mode: string
  mode_label: string
  network: boolean
  /** 界面改过没有 —— 用来解释「为什么我改了 .env 却没反应」。 */
  customized: boolean
  available: boolean
  backend: string
  unavailable_reason: string
  modes: SandboxModeOption[]
}

/**
 * 顶栏右侧的「执行权限」入口 —— 沙箱档位与联网开关。
 *
 * 为什么放顶栏，而不是输入框上方那一排（模式 / 模型 / 工作目录 / 附件）：
 * 那一排全是**这一轮**的选择，而沙箱是**整台机器**的边界 —— 改完对所有任务生效，
 * 正在跑的那一轮也会用上新档位。把全局开关塞进一排单轮选项里，用户会以为它只对
 * 当前这次对话起作用，于是要么不敢动、要么被别的任务随后的行为搞糊涂。
 *
 * 也因为它动的是安全边界，弹窗里改的是**草稿**，点保存才落库：随手点一下就把
 * 隔离关掉，是这里最不该提供的便利。
 */
const open = ref(false)
const status = ref<SandboxStatus | null>(null)
const error = ref('')
const saving = ref(false)

const draftMode = ref('')
const draftNetwork = ref(false)

const { t } = useI18n()

/**
 * 档位的显示名与说明。**按 `mode` 的值查前端文案**，不用后端随清单给的那份 ——
 * 界面文案要跟着用户选的语言走，而后端并不知道用户此刻用的是哪种语言
 * （后端那份 label / hint 留给回执这类非界面场合）。
 *
 * 「有哪些档位」仍然由后端说了算（清单是它给的），这里只负责把值翻成话。
 */
function modeLabel(value: string | undefined): string {
  if (value === 'read-only') return t('sandbox.mode.readOnly.label')
  if (value === 'workspace-write') return t('sandbox.mode.workspaceWrite.label')
  return t('sandbox.mode.off.label')
}

function modeHint(value: string | undefined): string {
  if (value === 'read-only') return t('sandbox.mode.readOnly.hint')
  if (value === 'workspace-write') return t('sandbox.mode.workspaceWrite.hint')
  return t('sandbox.mode.off.hint')
}

/**
 * 档位名（顶栏那个）。取不到状态时给一个占位符，**不能回落到「执行权限」** ——
 * 模板里已经写了那个前缀，回落的结果是「执行权限：执行权限」。
 *
 * 这个分支在真机上只是接口没返回时的一瞬，但它会实实在在画出来（比如后端刚起、
 * 或某个接口挂了），而顶栏这一处是最不该出现自相矛盾文案的地方。
 */
const label = computed(() => (status.value ? modeLabel(status.value.mode) : '—'))

/** 关着就是开着的锁 —— 不点开弹窗也能一眼看出现在有没有边界。 */
const icon = computed(() => (status.value?.mode === 'off' ? Unlock : Lock))

/**
 * 按钮的色调，决定它用哪个颜色（见 style.css 里那三组 `--sandbox-*`）。
 *
 * 三种档位给三种颜色，而不是统一用灰字：这是**安全边界此刻的取值**，扫一眼就该
 * 知道现在放得多松。统一灰字等于要求用户点开弹窗才知道 —— 而这个值的意义恰恰在于
 * 不用点开。
 *
 * 「后端不可用」另算一种色：那时每条命令都会被拒绝，用户看到的是「程序坏了」
 * 而不是「我设错了一档」，比档位本身更该被注意到。
 */
const tone = computed(() => {
  if (!status.value) return 'off'
  if (!status.value.available && status.value.mode !== 'off') return 'broken'
  return status.value.mode
})

/** 要隔离、但这台机器没有后端 —— 那样命令会被**拒绝执行**，得在保存前就说清楚。 */
const backendMissing = computed(
  () => Boolean(status.value) && !status.value!.available && draftMode.value !== 'off',
)

const title = computed(() => {
  if (!status.value) return t('sandbox.label')
  const base = t('sandbox.labelValue', { value: label.value })
  return status.value.available || status.value.mode === 'off'
    ? base + t('sandbox.tipClick')
    : base + t('sandbox.tipNoBackend')
})

/**
 * 当前选中档位管什么。
 *
 * 按档位的**值**查前端文案，不用后端随清单给的那份（理由见上面 `modeLabel`）。
 * 清单本身仍以后端为准 —— 加一档不该改前端。
 */
const hint = computed(() => (status.value ? modeHint(draftMode.value) : ''))

async function load(): Promise<void> {
  error.value = ''
  try {
    status.value = await api.get<SandboxStatus>('/sandbox')
    draftMode.value = status.value.mode
    draftNetwork.value = status.value.network
  } catch (exc) {
    error.value = errorText(exc)
  }
}

onMounted(load)

function openDialog(): void {
  error.value = ''
  if (status.value) {
    // 把草稿拉回当前值：上次改了一半又关掉，不该留在那儿
    draftMode.value = status.value.mode
    draftNetwork.value = status.value.network
  } else {
    void load()
  }
  open.value = true
}

async function save(): Promise<void> {
  saving.value = true
  error.value = ''
  try {
    // 后端回的是改完的完整状态，不必再补一次 GET
    status.value = await api.put<SandboxStatus>('/sandbox', {
      mode: draftMode.value,
      network: draftNetwork.value,
    })
    open.value = false
  } catch (exc) {
    // 留着弹窗和草稿：用户改的东西不该因为一次失败就没
    error.value = errorText(exc)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-button
    class="sandbox"
    :class="`tone-${tone}`"
    size="small"
    :icon="icon"
    :title="title"
    @click="openDialog"
  >
    <span class="label">{{ t('sandbox.labelValue', { value: label }) }}</span>
  </el-button>

  <!-- append-to-body 必须留着（理由见 HelpButton 里那段说明）：这个入口在顶栏，
       不挂到 body 的话弹窗会被顶栏那块玻璃板的层叠上下文困住 -->
  <el-dialog v-model="open" :title="t('sandbox.dialogTitle')" width="560px" append-to-body>
    <el-alert v-if="error" type="error" :closable="false" :title="error" class="block" />

    <!-- 这两段带 <strong> / <code>，文案在 locales 里，所以用 v-html 渲染 ——
         这些字符串是我们自己写的，不含用户输入。scoped 样式够不到 v-html 的内容，
         相关选择器因此都写了 :deep()（见下面样式） -->
    <p class="desc" v-html="t('sandbox.intro')" />
    <p class="desc note" v-html="t('sandbox.notApproval')" />

    <el-radio-group v-model="draftMode" class="modes">
      <el-radio-button v-for="item in status?.modes ?? []" :key="item.value" :value="item.value">
        {{ modeLabel(item.value) }}
      </el-radio-button>
    </el-radio-group>
    <p v-if="hint" class="muted hint">{{ hint }}</p>

    <div class="row">
      <el-switch v-model="draftNetwork" />
      <span>{{ t('sandbox.network') }}</span>
    </div>
    <p class="muted hint">{{ t('sandbox.networkHint') }}</p>

    <el-alert
      v-if="backendMissing"
      type="warning"
      :closable="false"
      class="block"
      :title="t('sandbox.noBackend')"
      :description="`${status?.unavailable_reason ?? ''} ${t('sandbox.noBackendSuffix')}`"
    />

    <p v-if="status?.customized" class="muted scope" v-html="t('sandbox.fromUi')" />

    <p class="muted scope" v-html="t('sandbox.machineWide')" />

    <template #footer>
      <el-button @click="open = false">{{ t('sandbox.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ t('sandbox.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
/* 有边框、有底色才像个能点的东西。
 *
 * 原来这里是 text 按钮（只有一行字、没有框），摆在顶栏上读起来就是一段说明文字 ——
 * 用户不会想到它可点。改成普通按钮形态，文字色交给下面那几档。 */
.sandbox {
  flex-shrink: 0;
  margin-right: 2px;
  font-weight: 600;

  /* 底色走 --btn-bg：深色模式下它比 --bg-card 深得多。
   * 三档权限色都是彩色文字，压在半透明白底上读不出来（用户反馈） */
  --el-button-bg-color: var(--btn-bg);
  --el-button-border-color: var(--border);
  --el-button-hover-bg-color: var(--btn-bg-hover);
  --el-button-active-bg-color: var(--btn-bg-hover);
}

/* 三档三种文字色（理由见脚本里的 `tone`）。
 *
 * 每档都要写 text / hover-text / active-text 三个变量：只写第一个的话，鼠标一悬停
 * Element 就把文字压回默认色，等于悬停时反而看不出是哪一档。悬停再把边框也染上
 * 同色，作为「这个按钮属于哪一档」的第二个提示。 */
.tone-off {
  --el-button-text-color: var(--sandbox-off);
  --el-button-hover-text-color: var(--sandbox-off);
  --el-button-active-text-color: var(--sandbox-off);
  --el-button-hover-border-color: var(--sandbox-off);
}

.tone-read-only {
  --el-button-text-color: var(--sandbox-read-only);
  --el-button-hover-text-color: var(--sandbox-read-only);
  --el-button-active-text-color: var(--sandbox-read-only);
  --el-button-hover-border-color: var(--sandbox-read-only);
}

.tone-workspace-write {
  --el-button-text-color: var(--sandbox-workspace-write);
  --el-button-hover-text-color: var(--sandbox-workspace-write);
  --el-button-active-text-color: var(--sandbox-workspace-write);
  --el-button-hover-border-color: var(--sandbox-workspace-write);
}

/* 命令会被全部拒绝的状态：不改档位色而是另给一个，因为它比「现在多松」更要紧 ——
   用户看到的现象是每条命令都失败，很容易当成程序坏了 */
.tone-broken {
  --el-button-text-color: var(--el-color-danger);
  --el-button-hover-text-color: var(--el-color-danger);
  --el-button-active-text-color: var(--el-color-danger);
  --el-button-hover-border-color: var(--el-color-danger);
}

.desc {
  margin: 0 0 8px;
  font-size: var(--fs-sm);
}

.desc.note {
  color: var(--text-soft);
}

.modes {
  margin-top: 12px;
}

.hint {
  margin: 6px 0 0;
  font-size: var(--fs-xs);
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 16px 0 0;
  font-size: var(--fs-sm);
}

.block {
  margin: 12px 0;
}

.scope {
  margin: 12px 0 0;
  font-size: var(--fs-xs);
}

/* `code` 来自 v-html 的文案里（`.scope` 本身是模板元素，不需要 :deep）——
 * scoped 样式够不到 v-html 生成的内容，漏了这条那两个 `.env` / `SANDBOX_MODE`
 * 会退化成没样式的裸文本 */
.scope :deep(code) {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--bg-soft);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: var(--fs-2xs);
}

/* 窄窗口下让位：页面标题和提示比这个状态更重要，图标留着就够认 */
@media (max-width: 1100px) {
  .sandbox .label {
    display: none;
  }
}
</style>
