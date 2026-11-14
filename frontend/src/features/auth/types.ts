/** 文件职责：声明登录请求、令牌响应、用户角色和当前用户信息的数据类型。
 * 作用与配合：为认证 API、共享上下文和 AuthProvider 提供一致的 TypeScript 数据约定。
 * 使用位置：认证模块构造登录参数、接收接口响应和组合会话信息时。
 * 边界：只描述数据结构，不校验运行时响应，也不授予角色权限。
 */
/** 登录请求的数据结构，对应后端登录接口的请求体。 */
export type LoginRequest = {
  // 用户注册时使用的邮箱。
  email: string

  // 本次登录输入的密码，不应保存到浏览器存储。
  password: string
}

/** 登录成功后，后端返回的数据结构。 */
export type LoginResponse = {
  // 后续访问受保护接口时使用的 JWT。
  access_token: string

  // 后端固定返回 bearer，表示使用 Bearer 令牌认证。
  token_type: 'bearer'
}

/** 后端允许的角色：普通业务用户、主管、管理员。 */
export type UserRole = 'agent' | 'supervisor' | 'admin'

/** GET /v1/auth/me 返回的当前用户信息。 */
export type UserResponse = {
  // 用户唯一 ID。
  id: string

  // 用户邮箱。
  email: string

  // 界面上显示的名字。
  display_name: string

  // 后端确认的真实角色，不能由前端自行指定。
  role: UserRole

  // 账号是否启用。
  is_active: boolean

  // JSON 中的日期时间是字符串，不是 JavaScript Date 对象。
  created_at: string
}
