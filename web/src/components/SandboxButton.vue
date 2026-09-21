<script setup lang="ts">
import { Lock, Unlock } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'

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

const label = computed(() => status.value?.mode_label ?? '执行权限')

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
  if (!status.value) return '执行权限'
  const base = `执行权限：${status.value.mode_label}`
  return status.value.available || status.value.mode === 'off'
    ? `${base}（点击修改）`
    : `${base}（这台机器没有可用的沙箱后端，命令会被拒绝执行）`
})

/** 当前选中档位管什么。文案由后端给（见 `SandboxMode.hint`），前端不另抄一份。 */
const hint = computed(
  () => status.value?.modes.find((item) => item.value === draftMode.value)?.hint ?? '',
)

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
    <span class="label">执行权限：{{ label }}</span>
  </el-button>

  <el-dialog v-model="open" title="执行权限（沙箱）" width="560px">
    <el-alert v-if="error" type="error" :closable="false" :title="error" class="block" />

    <p class="desc">
      沙箱限制<strong>命令够得着什么</strong> —— 由操作系统内核执行，越界的写入和联网
      直接失败，和模型怎么写命令无关。
    </p>
    <p class="desc note">
      它和「危险命令先问用户」那套审批<strong>不是一回事</strong>：审批管
      <strong>要不要问</strong>，沙箱管<strong>够不够得着</strong>。两者互相独立。
    </p>

    <el-radio-group v-model="draftMode" class="modes">
      <el-radio-button v-for="item in status?.modes ?? []" :key="item.value" :value="item.value">
        {{ item.label }}
      </el-radio-button>
    </el-radio-group>
    <p v-if="hint" class="muted hint">{{ hint }}</p>

    <div class="row">
      <el-switch v-model="draftNetwork" />
      <span>允许命令联网</span>
    </div>
    <p class="muted hint">
      默认断网：数据外传是这类 Agent 最实际的风险，而多数编码任务用不上网络
      （装依赖那一下可以临时打开）。
    </p>

    <el-alert
      v-if="backendMissing"
      type="warning"
      :closable="false"
      class="block"
      title="这台机器没有可用的沙箱后端"
      :description="
        `${status?.unavailable_reason ?? ''} 选「不隔离」以外的档位，会让所有命令被拒绝执行。`
      "
    />

    <p v-if="status?.customized" class="muted scope">
      当前档位是<strong>在界面上设的</strong>，它会盖过 <code>.env</code> 里的
      <code>SANDBOX_MODE</code>。
    </p>

    <p class="muted scope">
      这是<strong>整台机器</strong>的设置：保存后对所有任务生效，正在跑的那一轮也会在
      下一条命令上换用新档位。它不属于某个对话，所以入口在顶栏、不在输入框那一排。
    </p>

    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
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

  --el-button-bg-color: var(--bg-card);
  --el-button-border-color: var(--border);
  --el-button-hover-bg-color: var(--bg-hover);
  --el-button-active-bg-color: var(--bg-hover);
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
  font-size: 13px;
}

.desc.note {
  color: var(--text-soft);
}

.modes {
  margin-top: 12px;
}

.hint {
  margin: 6px 0 0;
  font-size: 12px;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 16px 0 0;
  font-size: 13px;
}

.block {
  margin: 12px 0;
}

.scope {
  margin: 12px 0 0;
  font-size: 12px;
}

.scope code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--bg-soft);
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;
  font-size: 11px;
}

/* 窄窗口下让位：页面标题和提示比这个状态更重要，图标留着就够认 */
@media (max-width: 1100px) {
  .sandbox .label {
    display: none;
  }
}
</style>
