<template>
  <!-- 分享短链统一入口：/<code> = 谱系分享(3D 谱系画布)；/d<code> = 仪表盘分享；其余 → 无效页 -->
  <DashboardShareView v-if="kind === 'dashboard'" />
  <Tree3D v-else-if="kind === 'tree'" />
  <div v-else class="share-invalid-wrap">
    <div class="share-invalid-card">
      <div class="share-invalid-icon">🔗</div>
      <h2>分享链接无效</h2>
      <p>该链接不存在、已失效或格式不正确，请联系分享方获取最新链接。</p>
      <a href="/" class="share-invalid-back">返回家谱首页</a>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import Tree3D from './Tree3D.vue'
import DashboardShareView from './DashboardShareView.vue'

const route = useRoute()

/** 短链形态判定：图谱树 = 6 位短码；仪表盘 = d + 6 位短码；其余视为无效 */
const kind = computed<'tree' | 'dashboard' | 'invalid'>(() => {
  const c = String(route.params.code || '').toUpperCase()
  if (c.length === 7 && c.startsWith('D')) return 'dashboard'
  if (c.length === 6) return 'tree'
  return 'invalid'
})

// 分享页标记：让请求拦截器对 401（无效/过期）只提示、不跳登录
;(window as any).__isSharePage = true
onBeforeUnmount(() => {
  ;(window as any).__isSharePage = false
})
</script>

<style scoped>
.share-invalid-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #102a43 0%, #1c3a5a 60%, #2d4a68 100%);
  padding: 24px;
}
.share-invalid-card {
  background: #fff;
  border-radius: 12px;
  padding: 40px 48px;
  text-align: center;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  max-width: 420px;
}
.share-invalid-icon {
  font-size: 44px;
  margin-bottom: 8px;
}
.share-invalid-card h2 {
  margin: 0 0 12px;
  color: #1c3a5a;
}
.share-invalid-card p {
  color: #666;
  margin: 0 0 20px;
  line-height: 1.7;
}
.share-invalid-back {
  display: inline-block;
  color: #fff;
  background: #1677ff;
  padding: 8px 20px;
  border-radius: 6px;
  text-decoration: none;
}
.share-invalid-back:hover {
  background: #4096ff;
}
</style>
