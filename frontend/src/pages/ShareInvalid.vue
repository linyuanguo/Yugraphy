<template>
  <!-- 旧 /s/、/visit?token= 及未知路径 → 统一「链接无效」页（旧分享形态已下线） -->
  <div class="invalid-wrap">
    <div class="invalid-card">
      <div class="invalid-icon">🔗</div>
      <h2>链接无效</h2>
      <p>
        此链接指向的旧版分享形态（/s/ 或 /visit?token=）已停用，或页面不存在。<br />
        请联系分享方重新获取最新分享链接。
      </p>
      <a href="/" class="invalid-back">返回家谱首页</a>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.invalid-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #102a43 0%, #1c3a5a 60%, #2d4a68 100%);
  padding: 24px;
}
.invalid-card {
  background: #fff;
  border-radius: 12px;
  padding: 40px 48px;
  text-align: center;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  max-width: 480px;
}
.invalid-icon {
  font-size: 44px;
  margin-bottom: 8px;
}
.invalid-card h2 {
  margin: 0 0 12px;
  color: #1c3a5a;
}
.invalid-card p {
  color: #666;
  margin: 0 0 20px;
  line-height: 1.8;
}
.invalid-back {
  display: inline-block;
  color: #fff;
  background: #1677ff;
  padding: 8px 20px;
  border-radius: 6px;
  text-decoration: none;
}
.invalid-back:hover {
  background: #4096ff;
}
</style>
