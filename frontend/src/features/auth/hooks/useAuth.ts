/** 文件职责：为 React 组件提供读取共享登录状态和认证操作的统一 Hook。
 * 作用与配合：消费 AuthProvider 提供的 AuthContext，缺少 Provider 时明确报告接入错误。
 * 使用位置：登录页、路由守卫、工作台布局和入库相关组件及 Hook。
 * 边界：只读取并返回上下文，不建立会话、不发起认证请求，也不自行持有登录状态。
 */
// React 提供的读取上下文的方法。
import { useContext } from 'react'

// 导入之前创建的登录上下文。
import { AuthContext } from '../auth-context'

// 导入共享数据和方法的类型。
import type { AuthContextValue } from '../auth-context'

/**
 * 读取当前应用共享的登录状态和方法。
 *
 * 不需要参数，因为固定读取 AuthContext。
 * 必须在 AuthProvider 包含的组件中使用。
 */
export function useAuth(): AuthContextValue {
  // 读取上层 Provider 提供的数据。
  const context = useContext(AuthContext)

  // null 表示缺少 Provider，是程序接入问题。
  // 它不表示“用户没有登录”。
  if (context === null) {
    throw new Error('useAuth 必须在 AuthProvider 内使用')
  }

  // 返回登录状态、当前会话，以及登录和退出等方法。
  return context
}
