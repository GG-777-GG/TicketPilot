/** 文件职责：封装登录和查询当前用户的认证接口。
 * 作用与配合：复用 requestJson 发送请求，为 AuthProvider 返回令牌或用户信息。
 * 使用位置：AuthProvider 的登录流程和页面刷新后的会话恢复流程。
 * 边界：只负责接口调用，不保存令牌、不管理共享状态，也不决定页面跳转。
 */
// 导入公共请求函数，复用超时和 HTTP 错误处理。
import { requestJson } from '../../shared/api/http'

// 这里只需要类型，因此使用 import type。
import type { LoginRequest, LoginResponse,UserResponse, } from './types'

/**
 * 使用邮箱和密码申请登录令牌。
 *
 * 流程：接收登录参数 → 发送 POST 请求 → 返回令牌响应。
 * 失败时继续抛出错误，由页面负责显示提示。
 * 本函数不保存令牌，也不负责页面跳转。
 */
export function loginUser(
  // 登录参数，必须包含 LoginRequest 规定的邮箱和密码。
  credentials: LoginRequest,

  // 可选的取消信号；调用者可以主动取消等待。
  signal?: AbortSignal,
): Promise<LoginResponse> {
  // 指定成功响应的预期类型为 LoginResponse。
  return requestJson<LoginResponse>('/v1/auth/login', {
    // 对应后端的 POST 登录接口。
    method: 'POST',

    headers: {
      // 告诉后端：本次请求体采用 JSON 格式。
      'Content-Type': 'application/json',
    },

    // JSON.stringify 是 JavaScript 自带的方法：
    // 把 JavaScript 对象转换成可以发送的 JSON 字符串。
    body: JSON.stringify({
      // 明确只提交这两个字段，避免带入后端不接受的字段。
      email: credentials.email,
      password: credentials.password,
    }),

    // 将取消信号交给公共请求工具；没传时就是 undefined。
    signal,
  })
}

/**
 * 使用 JWT 查询当前登录用户的信息。
 *
 * 流程：携带令牌 → 请求 /auth/me → 返回用户信息。
 * 后端根据令牌识别用户，因此不需要另外传用户 ID。
 * 请求失败时继续抛出错误，由调用方处理。
 */
export function getCurrentUser(
  // 登录接口返回的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 可选的取消信号，用于取消本次等待。
  signal?: AbortSignal,
): Promise<UserResponse> {
  return requestJson<UserResponse>('/v1/auth/me', {
    // 查询信息使用 GET，不需要发送请求体。
    method: 'GET',

    headers: {
      // 按 Bearer 认证格式，将 JWT 放入请求头。
      Authorization: `Bearer ${accessToken}`,
    },

    // 将取消信号传递给公共请求工具。
    signal,
  })
}
