/** 文件职责：统一读写和删除当前标签页 sessionStorage 中的访问令牌。
 * 作用与配合：供 AuthProvider 在登录、会话恢复和退出时管理本地令牌。
 * 使用位置：认证模块的浏览器存储边界，所有操作使用同一个存储名称。
 * 边界：只保存令牌字符串，不保存密码或用户资料，不校验令牌，也不撤销后端会话。
 */
// 令牌在浏览器存储中的名称。
// 这只是存储名称，不是 JWT 签名密钥，也不是令牌内容。
const ACCESS_TOKEN_KEY = 'ticketpilot.access_token'

/**
 * 读取当前标签页保存的访问令牌。
 * 没有保存过时返回 null。
 * 浏览器不允许访问存储时，错误交给调用者处理。
 */
export function readAccessToken(): string | null {
  // window 是浏览器窗口对象。
  // getItem() 根据存储名称读取字符串。
  return window.sessionStorage.getItem(ACCESS_TOKEN_KEY)
}

/**
 * 保存访问令牌，供刷新页面后恢复会话使用。
 * 应在后端确认用户身份后调用，不保存密码或用户角色。
 */
export function saveAccessToken(
  // JWT 字符串本身，不包含 Bearer 前缀。
  accessToken: string,
): void {
  // 去掉令牌首尾可能夹带的空白。
  const token = accessToken.trim()

  if (!token) {
    throw new Error('访问令牌不能为空')
  }

  // setItem(存储名称, 字符串内容)。
  // 同名记录已经存在时，会替换原来的值。
  window.sessionStorage.setItem(ACCESS_TOKEN_KEY, token)
}

/**
 * 删除当前标签页保存的访问令牌。
 * 只删除这一项，不清空其他业务保存的数据。
 * 删除本地令牌，不等于撤销后端已经签发的 JWT。
 */
export function clearAccessToken(): void {
  // removeItem() 删除指定名称的记录；记录不存在也可以调用。
  window.sessionStorage.removeItem(ACCESS_TOKEN_KEY)
}
