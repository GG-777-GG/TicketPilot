/** 文件职责：根据身份确认状态控制访客页面和受保护页面的显示或跳转。
 * 作用与配合：通过 useAuth 读取会话状态，使用 Navigate 跳转，并提供恢复失败后的重试入口。
 * 使用位置：应用路由中包裹登录页和工作台布局，分别采用 guest 与 protected 模式。
 * 边界：只处理前端路由访问体验，不重复请求认证接口，也不替代后端的权限检查。
 */
import type { ReactNode } from 'react'

// 渲染 Navigate 时，路由会跳转到指定地址。
import { Navigate } from 'react-router'

import { useAuth } from '../hooks/useAuth'

/** 路由守卫接收的参数。 */
type AuthGuardProps = {
  // protected：需要登录才能访问，例如工作台。
  // guest：供未登录用户访问，例如登录页。
  mode: 'protected' | 'guest'

  // 检查通过后，准备显示的页面或布局。
  children: ReactNode
}

/**
 * 根据共享登录状态，控制当前页面是否显示。
 *
 * 正在确认身份：显示等待提示。
 * 确认失败：显示错误和重试按钮。
 * 身份已确定：根据页面类型决定显示或跳转。
 */
export function AuthGuard({
  mode,
  children,
}: AuthGuardProps) {
  // 直接读取 Provider 的状态，不在守卫中重复请求登录接口。
  const {
    status,
    errorMessage,
    retryRestore,
  } = useAuth()

  // 刷新页面后，需要等待 /auth/me 确认身份。
  // 等待期间不提前显示工作台，也不急着跳到登录页。
  if (status === 'checking') {
    return (
      <p role="status">
        正在确认登录状态，请稍候……
      </p>
    )
  }

  // 网络异常不代表未登录，因此单独展示错误。
  if (status === 'error') {
    return (
      <section aria-label="登录状态确认失败">
        <p role="alert">
          {errorMessage ?? '暂时无法确认登录状态，请重试。'}
        </p>

        {/* 传入函数，点击时才执行，不要写成 retryRestore()。 */}
        <button type="button" onClick={retryRestore}>
          重新检查
        </button>
      </section>
    )
  }

  // 工作台要求登录：没有身份时转到登录页。
  if (mode === 'protected' && status === 'anonymous') {
    return <Navigate to="/login" replace />
  }

  // 已登录用户访问登录页：直接进入入库工作台。
  if (mode === 'guest' && status === 'authenticated') {
    return <Navigate to="/knowledge/ingestion" replace />
  }

  // 检查通过，显示被包住的内容。
  return <>{children}</>
}
