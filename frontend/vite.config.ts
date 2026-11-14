/**
 * 文件职责：配置 Vite 的 React 插件和本地开发服务器。
 * 作用与配合：将前端 /v1 请求转发到本机 8000 端口的 FastAPI 服务。
 * 使用位置：运行 npm run dev、npm run build 等 Vite 命令时读取。
 * 边界：开发代理不等于生产反向代理，也不负责启动后端或 Worker。
 */
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

    // 本地前端开发服务器的配置。
server: {
  // 将匹配指定路径的请求转发给后端。
  proxy: {
    // 以 /v1 开头的请求都走这个规则。
    '/v1': {
      // FastAPI 后端地址；保留原来的 /v1 请求路径。
      target: 'http://127.0.0.1:8000',

      // 转发时，让请求的 Host 头匹配后端地址。
      changeOrigin: true,
    },
  },
},
})
