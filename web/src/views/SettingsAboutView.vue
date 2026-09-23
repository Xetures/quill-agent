<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api } from '../api/client'
import quillIconLarge from '../assets/quill-icon-large.svg'

const { t } = useI18n()

const version = ref('')

onMounted(async () => {
  // 版本号只有探活接口有。拿不到就不显示，不值得为它加一个专门的端点
  try {
    const data = await api.get<{ version: string }>('/health')
    version.value = data.version
  } catch {
    version.value = ''
  }
})
</script>

<template>
  <div class="app-info">
    <!-- 大尺寸图标（1024，带羽毛细节的那版）：这里放得下，也就只有这里用得到 -->
    <img class="app-icon" :src="quillIconLarge" alt="" />
    <div>
      <div class="app-name">Quill</div>
      <div class="muted app-desc">{{ t('app.aboutDesc') }}{{ version ? ` · v${version}` : '' }}</div>
    </div>
  </div>
</template>

<style scoped>
.app-info {
  display: flex;
  align-items: center;
  gap: 14px;
}

.app-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
}

.app-name {
  font-size: var(--fs-lg);
  font-weight: 600;
}

.app-desc {
  font-size: var(--fs-xs);
}
</style>
