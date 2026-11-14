/**
 * 文件职责：封装任务查询、文件提交、取消和失败重试接口。
 * 作用与配合：复用 requestJson，携带 JWT，供业务 Hook 调用。
 * 使用位置：任务列表、文件上传、取消和重试功能。
 * 边界：不管理页面状态或缓存，不直接执行后台入库。
 */

// 复用公共请求工具，统一处理超时和 HTTP 错误。
import { requestJson } from '../../shared/api/http'

// 导入任务列表响应和任务状态的类型。
import type {
  FileIngestionJobInput,
  IngestionJobResponse,
  IngestionJobListResponse,
  IngestionJobStatus,
} from './types'


/**
 * 查询函数接收的参数。
 * 这些参数分别用于请求头、URL 和取消控制，
 * 不会全部作为请求体发送给后端。
 */
type ListIngestionJobsOptions = {
  // 当前会话中的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string

  // 每页最多返回多少条，后端允许 1～100；省略时使用 20。
  limit?: number

  // 跳过多少条任务；省略时从第 0 条开始。
  offset?: number

  // 可选的状态筛选；省略时查询所有状态。
  status?: IngestionJobStatus

  // 可选的取消信号，页面离开时可以停止等待请求。
  signal?: AbortSignal
}

/**
 * 分页查询当前用户有权查看的入库任务。
 *
 * 流程：组合查询参数 → 携带 JWT 发送 GET → 返回分页结果。
 * 请求失败时继续抛出，由调用方显示错误。
 */
export function listIngestionJobs(
  // 本次查询使用的令牌、分页、筛选和取消配置。
  options: ListIngestionJobsOptions,
): Promise<IngestionJobListResponse> {
  // URLSearchParams 是浏览器提供的查询参数工具。
  // String() 是 JavaScript 自带的函数，把数字转换成字符串。
  // ?? 表示没有提供值时，使用右边的默认值。
  const query = new URLSearchParams({
    limit: String(options.limit ?? 20),
    offset: String(options.offset ?? 0),
  })

  // 提供了具体状态，才将它加入查询参数。
  // 查询所有状态时，不发送 status，不能发送 status=all。
  if (options.status !== undefined) {
    // set() 添加或更新一个查询参数。
    query.set('status', options.status)
  }

  return requestJson<IngestionJobListResponse>(
    // toString() 将查询参数转换为 limit=20&offset=0 这样的文字。
    `/v1/knowledge/jobs?${query.toString()}`,
    {
      // 查询列表使用 GET，不发送请求体。
      method: 'GET',

      headers: {
        // 后端通过 JWT 确认身份和查询权限。
        Authorization: `Bearer ${options.accessToken}`,
      },

      // 交给公共请求工具处理取消信号。
      signal: options.signal,
    },
  )
}

/**
 * 上传原始文件，提交一条异步入库任务。
 *
 * 流程：组合上传表单 → 携带 JWT 发送 POST → 返回任务记录。
 * HTTP 202 表示请求已接收，不代表解析、OCR 或入库已经完成。
 * 失败时抛给调用方；超时不自动重发，任务是否创建需核实列表。
 */
export function submitFileIngestionJob(
  // 当前会话的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 上传所需的文件、标题和可见范围。
  input: FileIngestionJobInput,

  // 可选的取消信号，用于停止浏览器等待。
  signal?: AbortSignal,
): Promise<IngestionJobResponse> {
  // FormData 是浏览器提供的表单数据工具。
  // 这里创建空表单，用来组合准备上传的数据。
  const formData = new FormData()

  // append() 向表单添加“字段名 + 字段值”。
  // 字段名必须与后端接口的参数名称对应。
  formData.append('file', input.file)
  formData.append('title', input.title)
  formData.append('visibility', input.visibility)

  return requestJson<IngestionJobResponse>(
    '/v1/knowledge/jobs/upload',
    {
      // 提交新任务使用 POST。
      method: 'POST',

      headers: {
        Authorization: `Bearer ${accessToken}`,

        // 不手动设置 Content-Type。
        // 浏览器会为 FormData 自动补充 multipart 格式和边界。
      },

      // 文件上传发送 FormData，不使用 JSON.stringify。
      body: formData,

      // 将取消信号继续交给公共请求工具。
      signal,
    },

    // 上传最多等待 60 秒；这不是等待 Worker 完成入库。
    60_000,
  )
}

/**
 * 申请取消一条入库任务。
 *
 * 排队任务直接取消；
 * 运行中的任务先进入 cancelling，等待 Worker 安全收尾。
 * 请求失败时交给调用方处理，不自动重发。
 */
export function cancelIngestionJob(
  // 当前登录账号的 JWT 本身。
  accessToken: string,

  // 准备取消的任务 ID。
  jobId: string,

  // 控制浏览器停止等待，不等于取消后台任务。
  signal?: AbortSignal,
): Promise<IngestionJobResponse> {
  return requestJson<IngestionJobResponse>(
    // encodeURIComponent 是 JavaScript 自带函数。
    // 将 ID 编码成适合放进 URL 的路径片段。
    `/v1/knowledge/jobs/${encodeURIComponent(jobId)}/cancel`,
    {
      method: 'POST',

      headers: {
        Authorization: `Bearer ${accessToken}`,
      },

      // 后端通过路径获取任务 ID，不需要请求体。
      signal,
    },
  )
}

/**
 * 将失败的入库任务重新放回等待队列。
 *
 * 正常流程：
 * 携带 JWT 和任务 ID → 发送重试申请 → 返回重新排队的任务。
 *
 * 返回 HTTP 202 表示已排队，不代表入库成功。
 * 失败时继续抛给调用方，不自动重复发送。
 */
export function retryIngestionJob(
  // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 准备重新处理的失败任务 ID。
  jobId: string,

  // 可选的信号，用来停止浏览器等待本次请求。
  signal?: AbortSignal,
): Promise<IngestionJobResponse> {
  return requestJson<IngestionJobResponse>(
    // encodeURIComponent 将任务 ID 编码为 URL 路径片段。
    `/v1/knowledge/jobs/${encodeURIComponent(jobId)}/retry`,
    {
      // 重试会改变任务状态，因此使用 POST。
      method: 'POST',

      headers: {
        // 后端根据令牌检查身份和操作权限。
        Authorization: `Bearer ${accessToken}`,
      },

      // 任务 ID 已在路径中，不需要发送请求体。
      signal,
    },
  )
}

/**
 * 按 ID 查询一条当前用户有权查看的入库任务。
 *
 * 流程：
 * 携带 JWT 和任务 ID → 发送 GET 请求 → 返回任务当前信息。
 *
 * 查询不会修改任务，也不会执行入库。
 * 请求失败时交给调用方处理。
 */
export function getIngestionJob(
  // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 准备查看的任务 ID。
  jobId: string,

  // 可选信号，用于停止浏览器等待本次查询。
  signal?: AbortSignal,
): Promise<IngestionJobResponse> {
  return requestJson<IngestionJobResponse>(
    // encodeURIComponent 将任务 ID 编码为 URL 路径片段。
    `/v1/knowledge/jobs/${encodeURIComponent(jobId)}`,
    {
      // 读取任务信息使用 GET。
      method: 'GET',

      headers: {
        // 后端根据令牌检查身份和查看权限。
        Authorization: `Bearer ${accessToken}`,
      },

      // 继续传给公共请求工具和 fetch。
      signal,
    },
  )
}