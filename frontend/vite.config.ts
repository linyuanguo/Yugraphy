import { fileURLToPath, URL } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建版本：frontend/.build_version 记录当前版本号，每次 npm run build 自动 +1
// （vite dev 不写，只读取当前值展示）。该文件不提交 git（见仓库根 .gitignore），
// 各构建机各自维护计数；版本文件缺失时按 v10 起跳，即首次构建显示 v11。
const versionFile = path.resolve(process.cwd(), '.build_version')
const BASE_VERSION = 10

function currentVersion(): number {
  try {
    const n = parseInt(fs.readFileSync(versionFile, 'utf8').trim(), 10)
    return Number.isFinite(n) && n > 0 ? n : BASE_VERSION
  } catch {
    return BASE_VERSION
  }
}

export default defineConfig(({ command }) => {
  let version = currentVersion()
  if (command === 'build') {
    version += 1
    try {
      fs.writeFileSync(versionFile, String(version))
    } catch (err) {
      console.warn('[vite] 版本文件写入失败，本次版本号可能未递增：', err)
    }
  }
  return {
    plugins: [vue()],
    // 构建时注入版本号 + 时间戳，页面顶栏显示"版本 vN · 构建时刻"，用于确认加载的是最新版本
    define: {
      __BUILD_VERSION__: JSON.stringify(version),
      __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/files': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      chunkSizeWarningLimit: 2048,
    },
  }
})
