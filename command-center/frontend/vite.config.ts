import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// Tauri 需要固定的端口与严格模式
const host = process.env.TAURI_DEV_HOST

/**
 * 开发环境代理：把 /api 转发到本地后端，并自动注入访问令牌。
 *
 * 后端启动时会生成随机令牌写入 %APPDATA%/CapitalFlowCenter/cache/token，
 * 除 /api/health 外的接口都必须携带 —— 开发环境通过代理注入，
 * 浏览器里就不需要也不应该持有令牌。
 */
function backendProxyPlugin(): Plugin {
  const tokenFile = path.join(os.homedir(), 'AppData', 'Roaming', 'CapitalFlowCenter', 'cache', 'token')
  const portFile = path.join(os.homedir(), 'AppData', 'Roaming', 'CapitalFlowCenter', 'cache', 'port')

  const readToken = (): string => {
    try {
      return fs.readFileSync(tokenFile, 'utf8').trim()
    } catch {
      return ''
    }
  }
  const readPort = (): number => {
    try {
      return Number(fs.readFileSync(portFile, 'utf8').trim()) || 47821
    } catch {
      return 47821
    }
  }

  return {
    name: 'capital-flow-backend-proxy',
    configureServer(server) {
      server.middlewares.use('/api', async (req, res, next) => {
        if (!req.url) return next()
        const target = `http://127.0.0.1:${readPort()}/api${req.url}`
        const headers: Record<string, string> = {}
        for (const [k, v] of Object.entries(req.headers)) {
          if (typeof v === 'string' && k.toLowerCase() !== 'host') headers[k] = v
        }
        const token = readToken()
        if (token) headers['x-cf-token'] = token

        const chunks: Buffer[] = []
        for await (const c of req) chunks.push(c as Buffer)
        const body = chunks.length ? Buffer.concat(chunks) : undefined

        try {
          const resp = await fetch(target, {
            method: req.method,
            headers,
            body: body && body.length ? body : undefined,
          })
          res.statusCode = resp.status
          resp.headers.forEach((value, key) => {
            if (key.toLowerCase() !== 'content-length') res.setHeader(key, value)
          })
          const buf = Buffer.from(await resp.arrayBuffer())
          res.end(buf)
        } catch (err) {
          res.statusCode = 502
          res.end(JSON.stringify({ ok: false, message: `后端未就绪：${String(err)}` }))
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), backendProxyPlugin()],
  clearScreen: false,
  server: {
    port: 5183,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: 'ws', host, port: 5184 } : undefined,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  build: {
    // Tauri 使用 Chromium/WebView2，可安全使用现代语法
    target: 'chrome110',
    minify: 'esbuild',
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          react: ['react', 'react-dom'],
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    // 缓存写入项目内，避免依赖系统临时目录
    cacheDir: './node_modules/.vitest-cache',
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
  },
})
