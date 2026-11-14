/**
 * 文件职责：启动浏览器中的 React 应用，并加载全站基础样式。
 * 作用与配合：把 App 渲染到 index.html 的 root 容器，开发时启用 StrictMode 检查。
 * 使用位置：由 index.html 的模块脚本加载，是前端代码的启动入口。
 * 边界：不配置业务路由，不发送登录或任务查询请求。
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './shared/styles/global.css'

// 加载应用根组件。
import App from './app/App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
