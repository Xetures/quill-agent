<script setup lang="ts">
import { ArrowLeft, ArrowRight, Close, Delete } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { defineAsyncComponent, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { clearMemo, memo, persistMemo } from '../stores/memo'

const { t } = useI18n()

/**
 * 公共备忘录：从**左侧**拉出的一整块便签，和右侧的公共剪贴板对称。
 *
 * 对称的地方（照着 ClipboardPanel 抄的）：触发区都是**整条侧边空隙**（鼠标进到条里任何
 * 位置都算对准了它，点哪都能开合）、面板都撑满视口高度、都用同一份玻璃配方。
 * 只有内容相反：剪贴板是列表（被动收集的复制记录），这里是一块**自己写**的正文。
 *
 * 编辑器**按需加载**：CodeMirror 打出来 600 KB 上下（见那个组件的说明），
 * `defineAsyncComponent` 让它在第一次展开时才下载 —— 不展开的人一个字节都不花。
 */
const MarkdownEditor = defineAsyncComponent(() => import('./MarkdownEditor.vue'))

/**
 * 存盘节流。
 *
 * 输入是逐字符来的，每个字符写一次 localStorage 是拿磁盘换一个没人看的中间态；
 * 但也不能只在收起时写 —— 那种「写完就断电」的窗口太大了。400ms 是两者的折中：
 * 停手就落盘，连打时最多晚 400ms。
 */
const SAVE_DELAY = 400
const saveTimer = ref<number | undefined>()

function flush(): void {
  window.clearTimeout(saveTimer.value)
  saveTimer.value = undefined
  persistMemo()
}

function onInput(value: string): void {
  memo.text = value
  window.clearTimeout(saveTimer.value)
  saveTimer.value = window.setTimeout(() => {
    saveTimer.value = undefined
    persistMemo()
  }, SAVE_DELAY)
}

/** 收起面板：顺手把还没落盘的那一拍补上，别让它跟着组件一起被丢掉。 */
function close(): void {
  flush()
  memo.open = false
}

async function onClear(): Promise<void> {
  try {
    await ElMessageBox.confirm(t('memo.clearConfirm'), t('memo.clearTitle'), {
      confirmButtonText: t('memo.clear'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
  } catch {
    return // 用户点了取消
  }
  clearMemo()
}

// 面板是 v-if 的：收起时组件被卸载，兜住最后一次未落盘的输入
onBeforeUnmount(flush)
</script>

<template>
  <!-- 触发区：**整条左侧空隙**（任务区玻璃板左缘 ↔ 窗口左边界），和右边那条对称。
       箭头方向是镜像的：关着时朝右（往右拉出来），开着时朝左（推回去）。 -->
  <button
    class="memo-zone"
    :class="{ 'is-open': memo.open }"
    :title="memo.open ? t('memo.collapse') : t('memo.title')"
    @click="memo.open ? close() : (memo.open = true)"
  >
    <el-icon>
      <component :is="memo.open ? ArrowLeft : ArrowRight" />
    </el-icon>
  </button>

  <Transition name="memo-slide">
    <aside v-if="memo.open" class="memo-panel">
      <header class="memo-head">
        <span class="title">{{ t('memo.title') }}</span>
        <el-button
          text
          size="small"
          :icon="Delete"
          class="clear"
          :disabled="!memo.text"
          @click="onClear"
        >
          {{ t('memo.clear') }}
        </el-button>
        <el-button text size="small" :icon="Close" :title="t('memo.collapseShort')" @click="close" />
      </header>

      <div class="memo-body">
        <MarkdownEditor
          :model-value="memo.text"
          :placeholder="t('memo.placeholder')"
          min-height="70vh"
          @update:model-value="onInput"
        />
      </div>
    </aside>
  </Transition>
</template>

<style scoped>
/* 触发区：**整条左侧空隙**，从顶到底。
 *
 * 宽度取 16px —— 这正是 `.shell` 左侧 padding 留出来的那道空隙（见 App.vue），
 * 所以一像素也不会压到左边的侧栏；再宽就压上了。和右侧剪贴板那条同一个口径。 */
.memo-zone {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 1500;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  padding: 0;
  background: transparent;
  border: none;
  color: var(--text-faint);
  font-size: var(--fs-xs);
  cursor: pointer;
  transition: color 0.15s ease;
}

/* 高亮：**从中间往上下两端渐隐**（中间最实、越往上/下越淡）。
 * 一条均匀的色带从顶贯到底太生硬，像贴了张纸条；渐隐才像「这一条在发光」。
 *
 * 用伪元素 + opacity 过渡，而不是直接过渡 background —— 渐变本身没法做过渡动画 */
.memo-zone::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to bottom,
    rgba(255, 255, 255, 0) 0%,
    var(--bg-hover) 50%,
    rgba(255, 255, 255, 0) 100%
  );
  opacity: 0;
  transition: opacity 0.18s ease;
  pointer-events: none;
}

.memo-zone:hover,
.memo-zone.is-open {
  color: var(--accent);
}

.memo-zone:hover::before,
.memo-zone.is-open::before {
  opacity: 1;
}

/* 面板：紧贴箭头右侧拉出，**高度撑满整个视口** —— 它是「随手记」的东西，
 * 铺到顶和底就随时能写，不用先找个位置。
 *
 * 描边与圆角是**镜像**的：贴着窗口左边，所以只在右侧收圆角、左侧不画线。 */
.memo-panel {
  position: fixed;
  top: 0;
  left: 16px;
  bottom: 0;
  z-index: 1500;
  display: flex;
  flex-direction: column;
  width: 340px;
  overflow: hidden;

  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-left: none;
  border-radius: 0 var(--glass-radius) var(--glass-radius) 0;
  box-shadow: var(--glass-shadow);
}

.memo-slide-enter-active,
.memo-slide-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}

.memo-slide-enter-from,
.memo-slide-leave-to {
  transform: translateX(-16px);
  opacity: 0;
}

.memo-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding: 12px 16px 10px 12px;
  border-bottom: 1px solid var(--border);
}

.title {
  font-size: var(--fs-sm);
  font-weight: 600;
}

/* 「清空」推到右边，紧挨着收起按钮 */
.clear {
  margin-left: auto;
}

/* 正文区：flex 撑满，编辑器自己长高，滚交给这里 */
.memo-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px;
}

/* 编辑器铺满面板宽度：它自己的描边在这一层里显得多余（外面已经是玻璃板了） */
.memo-body :deep(.cm-editor) {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
}
</style>
