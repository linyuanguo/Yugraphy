<template>
  <a
    v-if="COPYRIGHT_TEXT"
    class="app-copyright"
    :class="`app-copyright--${mode}`"
    href="/license"
    target="_blank"
    rel="noopener"
    title="查看开源协议 / License"
  >
    {{ COPYRIGHT_TEXT }}
  </a>
</template>

<script setup lang="ts">
import { COPYRIGHT_TEXT } from '@/utils/copyright'

// mode: default=浅色内容区底；dark=深色侧边栏底；share=访客分享页(米黄底，需醒目)
withDefaults(defineProps<{ mode?: 'default' | 'dark' | 'share' }>(), { mode: 'default' })
</script>

<style scoped>
.app-copyright {
  display: block;
  font-size: 12px;
  color: #8c8c8c;
  text-align: center;
  line-height: 1.6;
  margin: 16px 0 4px;
  text-decoration: none;
  cursor: pointer;
  user-select: none;
  transition: color 0.2s;
}
.app-copyright:hover {
  color: #4a4a4a;
  text-decoration: underline;
}

/* 管理后台：深色侧边栏菜单栏底部 */
.app-copyright--dark {
  flex: none;
  margin: 0;
  padding: 10px 8px 12px;
  font-size: 11.5px;
  color: #93a1b5;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  line-height: 1.7;
}
.app-copyright--dark:hover {
  color: #d5dde8;
  text-decoration: underline;
}

/* 访客分享链接页：米黄背景上需醒目 */
.app-copyright--share {
  margin: 0;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #5a4428;
  background: rgba(255, 253, 248, 0.72);
  border-top: 1px solid rgba(74, 55, 40, 0.18);
  line-height: 1.8;
}
.app-copyright--share:hover {
  color: #8a5a1e;
  text-decoration: underline;
}
</style>
