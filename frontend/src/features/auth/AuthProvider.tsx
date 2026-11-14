/** 文件职责：管理登录、退出、刷新恢复和重试时的共享认证会话。
 * 作用与配合：协调认证 API、令牌存储与查询缓存，通过 AuthContext 提供状态和操作。
 * 使用位置：AppProviders 在查询客户端内部挂载它，供应用中的 useAuth 消费者使用。
 * 边界：负责前端会话生命周期，不渲染登录表单、不决定路由，也不替代后端鉴权。
 */
// React 提供的函数缓存、生命周期、引用和状态工具。
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
// TanStack Query 提供的 Hook，用于读取外层的查询客户端。
import { useQueryClient } from '@tanstack/react-query'

// 公共 HTTP 错误类型。
import { ApiError } from '../../shared/api/http'

// 身份认证接口。
import { getCurrentUser, loginUser } from './api'

// 登录状态的共享通道及类型。
import { AuthContext } from './auth-context'
import type {
  AuthContextValue,
  AuthSession,
  AuthStatus,
} from './auth-context'

// 浏览器令牌存储工具。
import {
  clearAccessToken,
  readAccessToken,
  saveAccessToken,
} from './token-storage'

import type { LoginRequest } from './types'

/** 将会话、状态和错误作为一组数据更新。 */
type AuthState = {
  session: AuthSession | null
  status: AuthStatus
  errorMessage: string | null
}

/** Provider 接收的参数。 */
type AuthProviderProps = {
  // 被这个 Provider 包含的组件和页面。
  children: ReactNode
}

/** 没有已确认的登录身份。 */
const ANONYMOUS_STATE: AuthState = {
  session: null,
  status: 'anonymous',
  errorMessage: null,
}

/** 正在向后端确认身份，不提前展示旧用户信息。 */
const CHECKING_STATE: AuthState = {
  session: null,
  status: 'checking',
  errorMessage: null,
}

/** 初始化时读取存储：无令牌直接显示登录页，有令牌才进行网络确认。 */
function readStoredAuth(): { accessToken: string | null; state: AuthState } {
  try {
    const accessToken = readAccessToken()?.trim() || null
    return {
      accessToken,
      state: accessToken ? CHECKING_STATE : ANONYMOUS_STATE,
    }
  } catch {
    return {
      accessToken: null,
      state: {
        session: null,
        status: 'error',
        errorMessage: '暂时无法读取登录状态，请检查浏览器存储设置后重试。',
      },
    }
  }
}

/**
 * 使用 JWT 确认用户身份，组合出会话对象。
 * 登录和刷新恢复都复用它。
 * 本函数不保存令牌，也不更新 React 状态。
 */
async function confirmSession(
  // 本次准备使用的 JWT。
  accessToken: string,

  // 用来取消本次身份确认等待的信号。
  signal: AbortSignal,
): Promise<AuthSession> {
  const user = await getCurrentUser(accessToken, signal)

  // 已经取消时立即抛出错误，不继续使用返回结果。
  signal.throwIfAborted()

  // 检查实际响应，不能只依赖 TypeScript 的类型声明。
  // includes() 判断角色是否在允许的列表中。
  if (
    !user ||
    typeof user.id !== 'string' ||
    !user.id ||
    typeof user.email !== 'string' ||
    typeof user.display_name !== 'string' ||
    typeof user.created_at !== 'string' ||
    user.is_active !== true ||
    !['agent', 'supervisor', 'admin'].includes(user.role)
  ) {
    throw new Error('用户信息响应格式不正确或账号不可用')
  }

  return { accessToken, user }
}

/**
 * 管理当前应用的登录会话，并通过 AuthContext 提供给子组件。
 * children 是被它包含的页面。
 */
export function AuthProvider({ children }: AuthProviderProps) {
  // 获取 AppProviders 中创建的同一个查询客户端。
  const queryClient = useQueryClient()
  // 初始化只读存储，不把未验证的令牌当成已登录身份。
  const [initialAuth] = useState(readStoredAuth)
  const [state, setState] = useState<AuthState>(initialAuth.state)

  // 记录当前正在执行的身份请求。
  // 新操作开始时，可以取消旧操作。
  const activeRequestRef = useRef<AbortController | null>(null)

  /**
   * 恢复浏览器中保存的会话。
   * useCallback 保持函数引用稳定，不是缓存接口结果。
   */
  const restoreSession = useCallback((accessToken: string): Promise<void> => {
    // 停止等待之前的身份请求。
    activeRequestRef.current?.abort()

    const controller = new AbortController()
    activeRequestRef.current = controller

    // 状态更新放在真实网络请求的完成回调中。
    return confirmSession(accessToken, controller.signal).then(session => {
      // 已取消，或者已经出现更新的操作时，不采用旧结果。
      if (
        controller.signal.aborted ||
        activeRequestRef.current !== controller
      ) {
        return
      }

      setState({
        session,
        status: 'authenticated',
        errorMessage: null,
      })
    }).catch((error: unknown) => {
      // 旧请求的错误，也不能影响新会话。
      if (
        controller.signal.aborted ||
        activeRequestRef.current !== controller
      ) {
        return
      }

      if (
        error instanceof ApiError &&
        (error.status === 401 || error.status === 403)
      ) {
        // /auth/me 的 401 表示身份失效，403 表示账号停用。
        try {
          clearAccessToken()

          setState({
            ...ANONYMOUS_STATE,
            errorMessage: error.message,
          })
        } catch {
          setState({
            session: null,
            status: 'error',
            errorMessage: '无法清除失效的登录状态，请检查浏览器存储设置。',
          })
        }
      } else {
        // 网络或服务故障不等于令牌失效。
        // 保留令牌以便重试，但不信任尚未确认的身份。
        setState({
          session: null,
          status: 'error',
          errorMessage:
            '暂时无法确认登录状态，请检查后端服务、网络或浏览器存储后重试。',
        })
      }
    }).finally(() => {
      // 只释放自己的请求，不能清掉后来出现的新请求。
      if (activeRequestRef.current === controller) {
        activeRequestRef.current = null
      }
    })
  }, [])

  // Provider 挂载时启动恢复；卸载时取消尚未完成的请求。
  useEffect(() => {
    queryClient.clear()
    if (initialAuth.accessToken) {
      void restoreSession(initialAuth.accessToken)
    }

    return () => {
      activeRequestRef.current?.abort()
      activeRequestRef.current = null
    }
  }, [initialAuth.accessToken, queryClient, restoreSession])

  /**
   * 建立新会话：登录 → 确认用户 → 保存令牌 → 更新共享状态。
   * 失败时抛回登录页面，由页面显示提示。
   */
  async function signIn(
    // 用户输入的邮箱和密码。
    credentials: LoginRequest,

    // 登录页面提供的可选取消信号。
    signal?: AbortSignal,
  ): Promise<void> {
    // 调用前就已取消时，不开始新登录。
    signal?.throwIfAborted()

    activeRequestRef.current?.abort()

    const controller = new AbortController()
    activeRequestRef.current = controller

    // Provider 或登录页面，任意一方都能取消本次等待。
    const requestSignal = signal
      ? AbortSignal.any([controller.signal, signal])
      : controller.signal

    setState(ANONYMOUS_STATE)

    try {
      requestSignal.throwIfAborted()

      // 开始新登录时，清除上一个会话的业务缓存。
      // 即使换成另一个账号登录，也不能沿用旧数据。
      queryClient.clear()

      // 新登录失败时，不继续保留之前的登录身份。
      clearAccessToken()

      const result = await loginUser(credentials, requestSignal)
      requestSignal.throwIfAborted()

      // 检查登录接口实际返回的令牌。
      if (
        !result ||
        typeof result.access_token !== 'string' ||
        !result.access_token.trim() ||
        result.token_type !== 'bearer'
      ) {
        throw new Error('登录响应格式不正确')
      }

      const session = await confirmSession(
        result.access_token.trim(),
        requestSignal,
      )

      requestSignal.throwIfAborted()

      // 防止旧登录覆盖后来的登录或退出操作。
      if (activeRequestRef.current !== controller) {
        throw new DOMException('登录请求已被替代', 'AbortError')
      }

      // 身份确认成功，而且请求仍有效时，才保存令牌。
      saveAccessToken(session.accessToken)

      setState({
        session,
        status: 'authenticated',
        errorMessage: null,
      })
    } finally {
      if (activeRequestRef.current === controller) {
        activeRequestRef.current = null
      }
    }
  }

  /** 退出当前本地会话，不代表撤销后端已经签发的 JWT。 */
  function signOut(): void {
    activeRequestRef.current?.abort()
    activeRequestRef.current = null
      // 清除 React 内存中的登录身份。
    // AuthGuard 读取到 anonymous 后，会自动跳转到登录页。
    setState(ANONYMOUS_STATE)

      // 清除本次会话的查询缓存和写操作缓存记录。
    queryClient.clear()

    try {
      clearAccessToken()
    } catch {
      // 清理失败时明确报告，不声称已经完整清理成功。
      setState({
        session: null,
        status: 'error',
        errorMessage: '无法清除本地登录状态，请检查浏览器存储设置。',
      })
    }
  }

  /** 会话恢复失败后，由用户主动点击重试。 */
  function retryRestore(): void {
    activeRequestRef.current?.abort()
    activeRequestRef.current = null
    queryClient.clear()

    // 重试来自按钮事件，在这里立即更新等待状态。
    const next = readStoredAuth()
    setState(next.state)
    if (next.accessToken) {
      void restoreSession(next.accessToken)
    }
  }

  // 将当前状态和操作方法组合成共享值。
  const value: AuthContextValue = {
    ...state,
    signIn,
    signOut,
    retryRestore,
  }

  // children 中的组件，以后就能通过 useAuth() 读取这些内容。
  return <AuthContext value={value}>{children}</AuthContext>
}
