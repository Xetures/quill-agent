<script setup lang="ts">
import { Folder, FolderOpened, Top } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { api } from '../api/client'
import type { BrowseResult } from '../api/types'
import { changeWorkdir, pickWorkdir, resetWorkdir, session } from '../stores/session'
import { errorText } from '../utils/error'

/**
 * 工具栏上只显示末级目录名。
 *
 * 完整路径放不下，也没必要 —— 挑目录时用户看的是名字，确认完整路径在弹层里。
 */
const currentName = computed(() => {
  const parts = session.workdir.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? '/'
})

/**
 * 只有空会话能换工作目录。
 *
 * 工作目录是文件工具的安全边界。聊到一半再换，历史里还留着旧目录下文件的内容，
 * 而新目录里的同名文件完全是另一个东西 —— 模型会拿着旧内容当新文件的答案，
 * 而且这种错很难从对话里看出来。
 */
const canChange = computed(() => session.messages.length === 0)

const open = ref(false)
const picking = ref(false)
const browsing = ref<BrowseResult | null>(null)

async function load(path = ''): Promise<void> {
  const query = path ? `?path=${encodeURIComponent(path)}` : ''
  browsing.value = await api.get<BrowseResult>(`/workdir/browse${query}`)
}

/**
 * 点按钮。
 *
 * 走哪条路由后端所在的环境决定：
 *   - 有系统对话框（本地跑）：直接拉起来 —— 这是用户最顺手的选目录方式
 *   - 没有（云端部署）：退回浏览器里的目录浏览
 *
 * 判断在**点击时**做而不是启动时，是因为那条路只在点下去的一瞬间才有意义；
 * 不过后端能力是启动时探好的（环境不会中途改变），这里只是读一个标志位。
 */
async function onClick(): Promise<void> {
  if (!canChange.value) return

  if (!session.nativePicker) {
    await openBrowser()
    return
  }

  picking.value = true
  try {
    const result = await pickWorkdir()

    // 缓存里记的是「可用」，后端这次却说不可用 —— 只可能是环境在中途变了
    // （比如服务重启后跑在了没有图形会话的地方）。退回浏览模式，比什么都不做好
    if (!result.available) {
      await openBrowser()
      return
    }

    if (result.error) {
      ElMessage.error(result.error)
      return
    }

    // path 为 null 表示用户按了取消，不是错误，静默收场
    if (result.path) {
      await changeWorkdir(result.path)
      // 提示里用切换后的值而不是 result.path：系统对话框给的路径带尾部斜杠
      // （`POSIX path of` 就这格式），后端规范化过的那份才是最终生效的
      ElMessage.success(`工作目录已切换到 ${session.workdir}`)
    }
  } catch (exc) {
    ElMessage.error(errorText(exc))
  } finally {
    picking.value = false
  }
}

async function openBrowser(): Promise<void> {
  open.value = true
  await load()
}

async function use(): Promise<void> {
  const path = browsing.value?.path
  if (!path) return

  try {
    await changeWorkdir(path)
    ElMessage.success(`工作目录已切换到 ${path}`)
    open.value = false
  } catch (exc) {
    // 会话可能刚好发了消息、或目录被删掉：后端文案比前端猜的准，原样显示
    ElMessage.error(errorText(exc))
  }
}

async function reset(): Promise<void> {
  await resetWorkdir()
  ElMessage.success('已恢复默认工作目录')
  await load()
}
</script>

<template>
  <el-button
    size="small"
    :icon="FolderOpened"
    :disabled="!canChange"
    :loading="picking"
    :title="canChange ? '选择工作目录' : '会话已经开始，工作目录不能再改（新建任务时可换）'"
    @click="onClick"
  >
    {{ currentName }}
  </el-button>

  <!-- 浏览器里的目录浏览，只在没有系统对话框时才会打开。
       用 dialog 而不是 popover：它是给「慢慢翻目录」用的，需要遮罩来收拢注意力 -->
  <el-dialog v-model="open" title="选择工作目录" width="420px" append-to-body>
    <div class="picker">
      <div class="browse-path" :title="browsing?.path ?? ''">{{ browsing?.path ?? '读取中…' }}</div>

      <div class="actions">
        <el-button
          size="small"
          text
          :icon="Top"
          :disabled="!browsing?.parent"
          @click="load(browsing?.parent)"
        >
          上一级
        </el-button>
        <el-button size="small" text @click="reset">恢复默认</el-button>
      </div>

      <el-scrollbar max-height="240px">
        <p v-if="browsing?.error" class="hint">{{ browsing.error }}</p>
        <p v-else-if="!browsing?.dirs.length" class="hint">没有子目录</p>

        <div
          v-for="dir in browsing?.dirs ?? []"
          :key="dir.path"
          class="dir"
          :title="dir.path"
          @click="load(dir.path)"
        >
          <el-icon><Folder /></el-icon>
          <span class="dir-name">{{ dir.name }}</span>
        </div>
      </el-scrollbar>
    </div>

    <template #footer>
      <!-- 浏览和切换刻意分成两步：点目录只是「进去看看」，要真正生效得再点一下，
           免得误点就把文件工具的边界给换了 -->
      <el-button size="small" @click="open = false">取消</el-button>
      <el-button size="small" type="primary" @click="use">用这个目录</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.browse-path {
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-soft);

  /* 长路径直接折行，完整显示。
   * 不用省略号的理由：省略末尾会看不到「当前在哪个目录」，而从左省略得靠
   * direction: rtl —— 那会把路径开头的 / 挤到行尾去（RTL 上下文里行首标点
   * 是「行尾」），显示出来的路径就不再是一个能照着输入的正确路径了。 */
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 4px;
}

.dir {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
}

.dir:hover {
  background: var(--bg-soft);
}

.dir-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hint {
  margin: 6px;
  font-size: 12px;
  color: var(--text-soft);
}
</style>
