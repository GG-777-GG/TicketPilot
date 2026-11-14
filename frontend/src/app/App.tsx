/**
 * 文件职责：组合公共 Provider 和路由，形成应用根组件。
 * 作用与配合：AppProviders 提供共享能力，RouterProvider 根据 router 显示页面。
 * 使用位置：由 main.tsx 挂载到浏览器页面。
 * 边界：不编写具体业务页面，也不直接请求后端接口。
 */
// RouterProvider 根据当前浏览器地址显示对应页面。
import { RouterProvider } from 'react-router/dom'

// 为所有路由页面提供共享的登录状态。
import { AppProviders } from './providers'

// 导入刚才定义的路由。
import { router } from './router'

/** 应用入口：交给路由决定当前显示哪个页面。 */
function App() {
  return (
  <AppProviders>
    {/* 路由显示的所有页面，都在登录状态容器内部。 */}
    <RouterProvider router={router} />
  </AppProviders>
)
}

export default App
