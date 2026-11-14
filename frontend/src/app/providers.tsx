/**
 * 文件职责：统一装配应用共享的查询客户端和登录状态容器。
 * 作用与配合：保持 QueryClient 实例稳定，将 QueryClientProvider 放在 AuthProvider 外层。
 * 使用位置：由 App.tsx 包含整个路由区域，让登录模块和业务页面共享这些能力。
 * 边界：只配置默认请求管理规则，不直接执行某项业务查询或绘制页面。
 */
//QueryClient	保存查询缓存和默认配置
// QueryClientProvider	向内部组件提供这个客户端
// AuthProvider	管理当前登录身份
// listIngestionJobs()	请求后端任务列表

// ReactNode 表示 React 可以显示的内容，例如页面、组件和文字。
// 它是类型，所以使用 import type。
import type { ReactNode } from 'react'

// 负责管理登录、退出和刷新后的身份恢复。
import { AuthProvider } from '../features/auth/AuthProvider'

// 使用状态保存同一个 QueryClient 实例。
import { useState } from 'react'

// QueryClient 管理查询缓存和配置。
// QueryClientProvider 把它提供给内部组件。
import {
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'

/** 应用公共状态容器接收的参数。 */
type AppProvidersProps = {
  // 被包在 AppProviders 标签里面的组件。
  children: ReactNode
}

/**
 * 统一装配整个应用需要的公共状态。
 * 目前接入登录状态，以后其他公共 Provider 也放在这里。
 */
export function AppProviders({ children }: AppProvidersProps) {
      // 使用初始化函数创建客户端，后续重新渲染时保留同一个实例。
  // 这里只需要读取客户端，不需要修改它，因此不接收 setter。
  const [queryClient] = useState(
    () =>
      new QueryClient({
        // 设置本应用的默认请求管理规则。
        defaultOptions: {
          // queries：查询数据，例如获取任务列表。
          queries: {
            // 查询失败后，不额外自动重试。
            // 后续的定时轮询会单独配置。
            retry: false,

            // 从其他窗口切回页面时，不因此自动重新查询。
            refetchOnWindowFocus: false,
          },

          // mutations：修改数据，例如提交上传任务、失败重试。
          mutations: {
            // 写操作失败后不自动重复发送，避免重复提交。
            retry: false,
          },
        },
      }),
  )
   return (
    <QueryClientProvider client={queryClient}>
      {/* 登录模块和业务页面都能访问同一个查询客户端。 */}
      <AuthProvider>
        {children}
      </AuthProvider>
    </QueryClientProvider>
  )
}
