<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Setting } from '@element-plus/icons-vue'

import { locale, setLocale, type Locale } from '../locales'
import { fontSize, setFontSize, type FontSize } from '../stores/font-size'
import {
  autoDark,
  autoLight,
  DARK_THEME_LIST,
  LIGHT_THEME_LIST,
  setAutoThemes,
  setTheme,
  themeMode,
  type ThemeMode,
} from '../stores/theme'

/**
 * 显示设置：明暗 / 字号 / 语言。
 *
 * 文件名还叫 SettingsThemeView、地址还是 `/settings/theme` —— 它原先只管配色，
 * 后来字号与语言也归到这一页（都是「在这台机器上显示成什么样」）。改名要动路由和
 * 既有链接，收益只是名字好看，所以留着；导航上叫「显示」。
 *
 * 三组用同一种卡片：一个预览块 + 名称 + 一句说明。预览各不相同 —— 配色看色块、
 * 字号看示例字、语言看一个「中 / A」，一眼能认出这张卡在说什么。
 *
 * 选项都由 `t()` 生成，所以是 computed 而不是模块级常量：语言一换就得重算。
 */
const { t } = useI18n()

const THEMES = computed<{ value: ThemeMode; label: string; hint: string }[]>(() =>
  ([
    'light',
    'forest',
    'amber',
    'sakura',
    'dark',
    'twilight',
    'ember',
    'aurora',
    'auto',
  ] as const).map((value) => ({
    value,
    label: t(`display.theme.${value}.label`),
    hint: t(`display.theme.${value}.hint`),
  })),
)

const SIZES = computed<{ value: FontSize; label: string; hint: string }[]>(() =>
  (['small', 'standard', 'large'] as const).map((value) => ({
    value,
    label: t(`display.fontSize.${value}.label`),
    hint: t(`display.fontSize.${value}.hint`),
  })),
)

const LANGUAGES = computed<{ value: Locale; label: string; hint: string }[]>(() =>
  (['zh-CN', 'en-US'] as const).map((value) => ({
    value,
    label: t(`display.language.${value}.label`),
    hint: t(`display.language.${value}.hint`),
  })),
)

/**
 * 「跟随系统」那张卡角上的齿轮：系统浅色时用哪一套、深色时用哪一套。
 *
 * 弹窗里改的是**草稿**，点了保存才落 —— 否则改一半关掉就已经生效了，而用户并没有
 * 确认。每次打开都从当前真值起手，免得上次那半个改动还留在框里。
 */
const autoDialogOpen = ref(false)
const autoLightDraft = ref<ThemeMode>(autoLight.value)
const autoDarkDraft = ref<ThemeMode>(autoDark.value)

watch(autoDialogOpen, (open) => {
  if (!open) return
  autoLightDraft.value = autoLight.value
  autoDarkDraft.value = autoDark.value
})

function saveAutoThemes(): void {
  setAutoThemes(autoLightDraft.value, autoDarkDraft.value)
  autoDialogOpen.value = false
}
</script>

<template>
  <div class="display">
    <section>
      <h2 class="group-title">{{ t('display.theme.title') }}</h2>
      <div class="cards">
        <!-- 卡片是 `div + role=button` 而不是 `<button>`：只有「跟随系统」这张要在标签
             那一行里再放一个齿轮按钮，而 HTML 不允许按钮里再套按钮（解析器会把外层的
             那个提前合上）。为此加了 tabindex 与回车/空格两个按键处理，别把它退化成
             「只能点」 -->
        <div
          v-for="item in THEMES"
          :key="item.value"
          class="card"
          :class="{ active: themeMode === item.value }"
          role="button"
          tabindex="0"
          @click="setTheme(item.value)"
          @keydown.enter.prevent="setTheme(item.value)"
          @keydown.space.prevent="setTheme(item.value)"
        >
          <!-- 用两个色块示意「侧边栏 + 主区」，比放一张真截图轻，换主题时也不用维护 -->
          <span class="preview" :class="item.value">
            <span class="preview-side" />
            <span class="preview-main" />
          </span>
          <span class="label-row">
            <span class="label">{{ item.label }}</span>
            <!-- 只有「跟随系统」这张卡有：它决定深浅各用哪一套，其余卡自己就是那一套 -->
            <button
              v-if="item.value === 'auto'"
              type="button"
              class="gear"
              :title="t('display.theme.autoGear')"
              @click.stop="autoDialogOpen = true"
            >
              <el-icon><Setting /></el-icon>
            </button>
          </span>
          <span class="hint">{{ item.hint }}</span>
        </div>
      </div>
    </section>

    <section>
      <h2 class="group-title">{{ t('display.fontSize.title') }}</h2>
      <div class="cards">
        <button
          v-for="item in SIZES"
          :key="item.value"
          type="button"
          class="card"
          :class="{ active: fontSize === item.value }"
          @click="setFontSize(item.value)"
        >
          <!-- 示例字按各自的倍数画，并且**跟着当前档位一起缩放**：
               这样不管当前是哪一档，三张卡之间的比例始终看得见（写死 px 就看不出来了） -->
          <span class="preview-sample" :class="item.value">Aa</span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>

    <section>
      <h2 class="group-title">{{ t('display.language.title') }}</h2>
      <div class="cards">
        <button
          v-for="item in LANGUAGES"
          :key="item.value"
          type="button"
          class="card"
          :class="{ active: locale === item.value }"
          @click="setLocale(item.value)"
        >
          <span class="preview-sample lang">{{ item.value === 'zh-CN' ? '中' : 'A' }}</span>
          <span class="label">{{ item.label }}</span>
          <span class="hint">{{ item.hint }}</span>
        </button>
      </div>
    </section>

    <!-- 「跟随系统」用哪两套。append-to-body 必须留着：页面在玻璃板里，弹窗若渲染在
         板内会被 backdrop-filter 影响（见 HelpButton.vue 里那段说明） -->
    <el-dialog
      v-model="autoDialogOpen"
      :title="t('display.theme.autoDialogTitle')"
      width="420px"
      append-to-body
    >
      <p class="auto-note">{{ t('display.theme.autoDialogNote') }}</p>
      <el-form label-position="top">
        <el-form-item :label="t('display.theme.autoLightLabel')">
          <el-select v-model="autoLightDraft" class="auto-select">
            <el-option
              v-for="value in LIGHT_THEME_LIST"
              :key="value"
              :label="t(`display.theme.${value}.label`)"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('display.theme.autoDarkLabel')">
          <el-select v-model="autoDarkDraft" class="auto-select">
            <el-option
              v-for="value in DARK_THEME_LIST"
              :key="value"
              :label="t(`display.theme.${value}.label`)"
              :value="value"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="autoDialogOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button size="small" type="primary" @click="saveAutoThemes">
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.display {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.group-title {
  margin: 0 0 10px;
  font-size: var(--fs-sm);
  font-weight: 600;
}

.cards {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 10px;
  width: 148px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease,
    transform 0.15s ease;
}

/* 标签那一行：齿轮跟在文字**右边**。早先把它压在卡片右上角，正好盖住了预览色块，
   看不清 —— 挪到文字旁边，既指得明白也不挡东西 */
.label-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.gear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  opacity: 0.65;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.gear:hover {
  opacity: 1;
}

.auto-note {
  margin: 0 0 14px;
  font-size: var(--fs-sm);
  line-height: 1.6;
}

.auto-select {
  width: 100%;
}

.card:hover {
  border-color: var(--accent);
  box-shadow: var(--shadow);
}

.card.active {
  border-color: var(--accent);
  /* 选中态用主色描边 + 一圈柔光，比换背景色克制 —— 卡片里已经有预览了 */
  box-shadow: 0 0 0 3px var(--accent-soft);
}

/* 所有预览块共用同一块「画布」：高度一致，三组卡片才对得齐 */
.preview,
.preview-sample {
  display: block;
  width: 100%;
  height: 56px;
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.preview {
  position: relative;
}

.preview-side {
  position: absolute;
  inset: 0 auto 0 0;
  width: 30%;
}

.preview-main {
  position: absolute;
  inset: 0 0 0 30%;
}

/* 预览卡片各用一组写死的颜色：展示各主题基调，不随当前主题变化 */
.preview.light .preview-side {
  background: #d8e7f4;
}
.preview.light .preview-main {
  background: #edf4fa;
}

.preview.forest .preview-side {
  background: #cee3d5;
}
.preview.forest .preview-main {
  background: #eaf4ed;
}

.preview.amber .preview-side {
  background: #f2dec2;
}
.preview.amber .preview-main {
  background: #fcf6eb;
}

.preview.sakura .preview-side {
  background: #f7d8e2;
}
.preview.sakura .preview-main {
  background: #fdf3f6;
}

.preview.dark .preview-side {
  background: #284260;
}
.preview.dark .preview-main {
  background: #192e45;
}

.preview.twilight .preview-side {
  background: #3c1e57;
}
.preview.twilight .preview-main {
  background: #201131;
}

.preview.ember .preview-side {
  background: #3d1b17;
}
.preview.ember .preview-main {
  background: #220f0d;
}

.preview.aurora .preview-side {
  background: #133535;
}
.preview.aurora .preview-main {
  background: #091d1e;
}

.preview.auto .preview-side {
  background: linear-gradient(160deg, #284260 50%, #d8e7f4 50%);
}
.preview.auto .preview-main {
  background: linear-gradient(160deg, #192e45 50%, #edf4fa 50%);
}

/* 字号与语言的预览：一块浅底，中间放示例字 */
.preview-sample {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-soft);
  color: var(--text);
  line-height: 1;
}

.preview-sample.small {
  font-size: calc(var(--fs-base) * var(--font-scale-small));
}
.preview-sample.standard {
  font-size: var(--fs-base);
}
.preview-sample.large {
  font-size: calc(var(--fs-base) * var(--font-scale-large));
}

/* 语言那组：示例字放大一号、用强调色，和字号那组的「Aa」区分开 */
.preview-sample.lang {
  font-size: var(--fs-xl);
  font-weight: 600;
  color: var(--gold);
}

.label {
  font-size: var(--fs-sm);
  font-weight: 500;
}

.card .hint {
  font-size: var(--fs-2xs);
  color: var(--text-soft);
  line-height: 1.4;
}
</style>
