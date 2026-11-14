/**
 *  文件职责：提供 JSON 和空响应的公共 HTTP 请求工具，以及 ApiError 错误类型。
 * 作用与配合：统一组合超时与取消信号，并将非成功 HTTP 响应转换为可识别的错误。
 * 使用位置：由 auth/api.ts、ingestion/api.ts 等业务接口模块调用。
 * 边界：不保存 JWT、不决定页面跳转，也不验证成功 JSON 的实际字段是否符合类型。
 */
/** 表示后端返回了非成功的 HTTP 状态码。 */
export class ApiError extends Error {
  // 保存状态码，方便页面区分 401、403、500 等情况。
  readonly status: number

  constructor(
    // 后端返回的 HTTP 状态码。
    status: number,

    // 准备提供给页面显示的错误说明。
    message: string,
  ) {
    // 调用父类 Error 的构造函数，保存错误说明。
    super(message)

    // 给这种错误设置名称，便于识别。
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * 统一发送请求，处理超时、停止等待信号和 HTTP 错误。
 *
 * 成功时返回浏览器的 Response 对象，交给外层函数处理正文。
 * HTTP 错误转换为 ApiError；网络失败、超时和取消继续抛出。
 * 不自动重试，也不保存令牌。
 */
async function requestResponse(
  // 接口路径，例如 /v1/auth/login。
  path: string,

  // 请求方法、请求头、请求体和停止等待信号。
  options: RequestInit = {},

  // 默认最长等待 15000 毫秒。
  timeoutMs = 15_000,
): Promise<Response> {
  // 创建一个超时信号，到达规定时间后取消浏览器等待。
  const timeoutSignal = AbortSignal.timeout(timeoutMs)

  // 如果调用者也提供了取消信号，就同时监听两个信号。
  // 任意一个触发，都取消本次请求等待。
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeoutSignal])
    : timeoutSignal

  // fetch 是浏览器提供的发送 HTTP 请求的方法。
  // await 表示等待请求得到响应。
  const response = await fetch(path, {
    // 默认不使用浏览器的 HTTP 缓存。
    cache: 'no-store',

    // 展开调用者传入的 method、headers、body 等配置。
    ...options,

    // 使用上面组合好的取消信号。
    signal,
  })

  // ok 在 HTTP 状态码为 200～299 时为 true。
  if (!response.ok) {
    // 尝试将错误响应解析为 JSON。
    // 如果响应不是 JSON，catch 就返回 null。
    // unknown 表示结构尚不确定，必须检查后才能读取字段。
    const body: unknown = await response.json().catch(() => null)

    // 后端没有提供明确说明时，使用默认错误信息。
    let message = `请求失败（HTTP ${response.status}）`

    // 先确认是对象、存在 detail，而且 detail 是字符串。
    if (
      body !== null &&
      typeof body === 'object' &&
      'detail' in body &&
      typeof body.detail === 'string'
    ) {
      // 读取 FastAPI 返回的错误说明。
      message = body.detail
    }

    // 抛出错误，让调用这个函数的地方处理。
    throw new ApiError(response.status, message)
  }

 // 返回完整 HTTP 响应；外层函数决定是否读取 JSON。
    return response
}

/**
 * 请求成功后读取 JSON，供登录、列表和详情等接口使用。
 * T 声明异步结果的类型，不负责校验实际 JSON 的字段。
 */
export async function requestJson<T>(
  // 业务接口路径。
  path: string,

  // HTTP 方法、请求头、请求体和停止等待信号。
  options: RequestInit = {},

  // 可选的超时毫秒数；省略时使用公共内部函数的默认值。
  timeoutMs?: number,
): Promise<T> {
  const response = await requestResponse(path, options, timeoutMs)

  // 将 JSON 解析为 JavaScript 数据。
  // as T 是类型断言，不是运行时字段校验。
  return (await response.json()) as T
}

/**
 * 请求成功后不读取正文，供归档等返回 HTTP 204 的接口使用。
 *
 * Promise<void> 表示成功时没有需要返回给调用方的数据。
 * 请求失败时继续抛出错误，不自动重复发送。
 */
export async function requestNoContent(
  // 业务接口路径。
  path: string,

  // HTTP 方法、请求头、请求体和停止等待信号。
  options: RequestInit = {},

  // 可选的超时毫秒数；省略时使用公共内部函数的默认值。
  timeoutMs?: number,
): Promise<void> {
  // 等待请求成功；空响应无需调用 response.json()。
  await requestResponse(path, options, timeoutMs)
}