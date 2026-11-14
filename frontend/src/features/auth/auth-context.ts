/** 文件职责：定义共享登录会话、身份状态和认证操作的类型及 React 上下文。
 * 作用与配合：由 AuthProvider 提供上下文值，useAuth 读取同一份状态和方法。
 * 使用位置：认证 Provider 与消费登录状态的 Hook 之间的数据约定。
 * 边界：只声明共享通道和契约，不执行登录请求、令牌存储或状态更新。
 */
// React 提供的创建上下文的方法。
import { createContext } from 'react'

// 导入登录参数和用户信息的类型。
import type { LoginRequest, UserResponse } from './types'

/** 前端组合出的登录会话，不是后端某个接口直接返回的结构。 */
export type AuthSession = {
  // 当前使用的 JWT，不显示到页面，也不打印到日志。
  accessToken: string

  // 通过 /auth/me 确认的用户信息。
  user: UserResponse
}

/** 整个应用的身份确认状态。 */
export type AuthStatus =
  // 启动或刷新后，正在检查已保存的令牌。
  | 'checking'
  // 当前没有已确认的登录身份。
  | 'anonymous'
  // 已经确认身份，此时 session 应当非空。
  | 'authenticated'
  // 会话恢复出错，例如网络或浏览器存储不可用。
  | 'error'

/** 共享通道需要提供的数据和方法。 */
export type AuthContextValue = {
  // 当前会话；没有已确认的身份时为空。
  session: AuthSession | null

  // 当前身份确认状态。
  status: AuthStatus

  // 会话恢复的错误提示；没有错误时为空。
  errorMessage: string | null

  // 登录方法的约定；具体实现放在后续的 AuthProvider。
  signIn(
    // 用户输入的邮箱和密码。
    credentials: LoginRequest,

    // 可选的取消信号。
    signal?: AbortSignal,
  ): Promise<void>

  // 退出登录，清理当前会话。
  signOut(): void

  // 会话恢复失败后，允许用户主动重试。
  retryRestore(): void
}

// 创建共享通道。
// null 表示还没有上层 Provider 提供数据，不是“默认登录成功”。
export const AuthContext =
  createContext<AuthContextValue | null>(null)
