<template>
  <div class="license-page">
    <header class="lp-top">
      <div class="lp-brand">
        <span class="lp-logo">谱</span>
        <span class="lp-name">家谱管理系统</span>
      </div>
      <div class="lp-actions">
        <a-button size="small" @click="goBack">← 返回</a-button>
        <a-button v-if="!isLoggedIn" size="small" type="primary" ghost @click="$router.push('/login')">
          登录系统
        </a-button>
      </div>
    </header>

    <main class="lp-body">
      <article class="lp-card">
        <h1 class="lp-title">License / 开源协议</h1>
        <p class="lp-lead">
          本项目（内网家谱管理系统，开发者：Yugo）基于 MIT License 开源授权。
        </p>

        <h2>一、项目声明</h2>
        <p>
          <strong>版权归属：</strong>© 2026 Yugo（开发者）。本项目由开发者免费开发，部署方拥有内部部署与业务使用的权利，但代码版权未发生转移。
        </p>
        <p>
          <strong>AI 辅助生成声明：</strong>本项目由开发者基于 AI 编程工具（CodeBuddy）辅助生成，开发者对项目的整体架构、逻辑集成及最终调试实现投入了实质性智力劳动，享有代码著作权。
        </p>
        <p>
          <strong>授权范围：</strong>MIT 协议授权任何组织与个人自由使用、复制、修改与分发软件源码；系统内录入的族谱家谱档案数据、扫描件及 AI 识别生成的内容不随源码授权，其权利归数据提供方所有。
        </p>
        <p>
          <strong>部署说明：</strong>系统当前部署于局域网内网；本 License 用于规范源码在协作与学习场景下的使用与再分发。
        </p>

        <h2>二、MIT License（英文原文 · 具有法律效力）</h2>
        <pre class="lp-code">Copyright (c) 2026 Yugo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.</pre>

        <h2>三、中文参考翻译（非官方 · 仅供参考）</h2>
        <blockquote class="lp-note">
          以下中文翻译仅供参考，不具有法律效力；如与英文原文有任何出入，以英文原文为准。
        </blockquote>
        <p><strong>MIT 许可证</strong></p>
        <p>版权所有 (c) 2026 Yugo</p>
        <p>
          特此免费授予任何获得本软件及相关文档文件（以下简称"本软件"）副本的人不受限制地处理本软件的权利，包括但不限于使用、复制、修改、合并、发布、分发、再许可和/或出售本软件副本的权利，并允许向其提供本软件的人这样做，但须满足以下条件：
        </p>
        <p>上述版权声明和本许可声明应包含在本软件的所有副本或实质性部分中。</p>
        <p>
          本软件按"原样"提供，不附带任何明示或默示的保证，包括但不限于对适销性、特定用途适用性和非侵权性的保证。在任何情况下，作者或版权持有人均不对因本软件、本软件的使用或其他与本软件相关的交易而产生的任何索赔、损害或其他责任承担责任，无论是基于合同的、侵权的或其他诉讼理由。
        </p>
      </article>
    </main>

    <footer class="lp-foot">© 2026 Yugo · MIT License · 家谱管理系统</footer>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const isLoggedIn = computed(() => auth.isLoggedIn)

// 从别的页面点进来的，直接返回上一页；直开地址则回首页
const goBack = () => {
  if (window.history.length > 1) window.history.back()
  else router.push(isLoggedIn.value ? '/' : '/login')
}
</script>

<style scoped>
.license-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f0e8;
  color: #2b2b2b;
}

/* 顶栏 */
.lp-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: #fff;
  border-bottom: 1px solid #e8e0d2;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  position: sticky;
  top: 0;
  z-index: 10;
}
.lp-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
  color: #4a3a24;
}
.lp-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  background: #7c5a32;
  color: #fff;
  font-size: 15px;
  flex: none;
}
.lp-actions {
  display: flex;
  gap: 8px;
}

/* 正文卡片 */
.lp-body {
  flex: 1;
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: 28px 20px 40px;
  box-sizing: border-box;
}
.lp-card {
  background: #fff;
  border-radius: 10px;
  padding: 36px 44px;
  box-shadow: 0 4px 18px rgba(90, 68, 40, 0.08);
  border: 1px solid #eee4d3;
  line-height: 1.9;
  font-size: 15px;
}
.lp-title {
  text-align: center;
  font-size: 26px;
  margin: 0 0 14px;
  color: #3d2f1b;
  letter-spacing: 1px;
}
.lp-lead {
  text-align: center;
  color: #6b5b43;
  margin: 0 0 28px;
}
.lp-card h2 {
  font-size: 18px;
  color: #7c5a32;
  border-left: 4px solid #a9824f;
  padding-left: 10px;
  margin: 30px 0 12px;
}
.lp-card p {
  margin: 8px 0;
}
.lp-code {
  background: #2d2a26;
  color: #e8e2d8;
  border-radius: 8px;
  padding: 18px 22px;
  font-size: 13px;
  line-height: 1.7;
  overflow-x: auto;
  white-space: pre;
  margin: 12px 0 4px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.lp-note {
  margin: 12px 0;
  padding: 10px 14px;
  background: #fbf4e6;
  border-left: 4px solid #d9a35c;
  color: #7a5c2e;
  border-radius: 0 6px 6px 0;
}

.lp-foot {
  text-align: center;
  padding: 16px;
  font-size: 12.5px;
  color: #9a8a6f;
}

@media (max-width: 640px) {
  .lp-card {
    padding: 22px 18px;
  }
}
</style>
