/**
 *  文件职责：封装知识文档列表、详情查询、归档和恢复接口。
 * 作用与配合：复用公共 HTTP 工具，携带 JWT，向 Hook 提供接口调用。
 * 使用位置：供知识文档查询和管理 Hook 调用。
 * 边界：不保存页面状态或缓存，不直接执行文件解析和入库。
 */

// JSON 接口读取数据；归档接口只等待成功响应。
import {
  requestJson,
  requestNoContent,
} from '../../shared/api/http'
// 分别表示文档列表响应和单篇文档详情响应。
// 文档列表、详情与历史版本列表的响应类型。
import type {
    KnowledgeDocumentListResponse,
    KnowledgeDocumentDetailResponse,
    KnowledgeDocumentVersionListResponse,
    KnowledgeDocumentVersionDetailResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentVersionRestoreRequest,
    KnowledgeDocumentTitleUpdateRequest,
} from './types'
/** 查询知识文档列表需要的参数。 */
type ListKnowledgeDocumentsOptions = {
    // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
    accessToken: string

    // 每页最多返回多少篇；省略时为 20，后端允许 1～100。
    limit?: number

    // 跳过多少篇文档；省略时从第 0 篇开始。
    offset?: number

    // 可选信号，用于停止浏览器等待本次查询。
    signal?: AbortSignal
}

/**
 * 分页查询当前账号有权管理的知识文档。
 *
 * 流程：组合分页参数 → 携带 JWT 发送 GET → 返回分页结果。
 * 当前后端只支持分页参数；请求失败时交给调用方处理。
 */
export function listKnowledgeDocuments(
    // 本次查询使用的令牌、分页和停止等待信号。
    options: ListKnowledgeDocumentsOptions,
): Promise<KnowledgeDocumentListResponse> {
    // URLSearchParams 是浏览器提供的查询参数工具。
    // String() 将数字转换为字符串，?? 在没有值时使用默认值。
    const query = new URLSearchParams({
        limit: String(options.limit ?? 20),
        offset: String(options.offset ?? 0),
    })

    return requestJson<KnowledgeDocumentListResponse>(
        // toString() 生成 limit=20&offset=0 这样的查询参数。
        `/v1/knowledge/documents?${query.toString()}`,
        {
            method: 'GET',

            headers: {
                Authorization: `Bearer ${options.accessToken}`,
            },

            signal: options.signal,
        },
    )
}

/**
 * 按文档 ID 查询一篇知识文档的详情。
 *
 * 流程：
 * 接收令牌和文档 ID → 携带 JWT 发送 GET → 返回详情。
 *
 * 后端检查当前账号是否为主管或管理员。
 * 请求失败时继续抛出，由后续 Hook 和页面处理。
 */
export function getKnowledgeDocument(
    // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
    accessToken: string,
    // 准备查看的知识文档 ID，不是入库任务 ID。
    documentId: string,
    // 可选信号，用于离开页面时停止浏览器等待请求。
    signal?: AbortSignal,
): Promise<KnowledgeDocumentDetailResponse> {
    return requestJson<KnowledgeDocumentDetailResponse>(
        // encodeURIComponent 是 JavaScript 自带函数。
        // 将 ID 编码成适合放入 URL 路径的文字。
        `/v1/knowledge/documents/${encodeURIComponent(documentId)}`,
        {
            // 查询详情使用 GET，文档 ID 已包含在路径中。
            method: 'GET',

            headers: {
                // 后端根据这个请求头确认用户身份和权限。
                Authorization: `Bearer ${accessToken}`,
            },

            // 将调用方提供的停止等待信号传给公共请求工具。
            signal,
        },
    )
}

/**
 * 根据文档 ID 申请归档知识文档。
 *
 * 流程：
 * 携带 JWT 和文档 ID → 发送 POST → 等待 HTTP 204。
 *
 * 归档后文档仍然保留，但停止参与正常知识检索。
 * 后端检查主管或管理员权限。
 * 请求失败时继续抛出，由后续 Hook 和页面处理。
 */
export function archiveKnowledgeDocument(
  // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 准备归档的知识文档 ID。
  documentId: string,

  // 可选信号，用于停止浏览器等待。
  // 停止等待不代表撤销后端已经执行的归档。
  signal?: AbortSignal,
): Promise<void> {
  return requestNoContent(
    // 将文档 ID 编码成适合放入 URL 路径的文字。
    `/v1/knowledge/documents/${encodeURIComponent(documentId)}/archive`,
    {
      // 归档会改变文档状态，因此使用 POST。
      method: 'POST',

      headers: {
        // 后端通过 JWT 确认身份和归档权限。
        Authorization: `Bearer ${accessToken}`,
      },

      // 文档 ID 已放在路径中，后端这个接口不要求请求体。
      signal,
    },
  )
}

/**
 * 恢复一篇已归档的知识文档，使其重新参与正常知识检索。
 *
 * 流程：
 * 携带 JWT 和文档 ID → 发送 POST → 后端检查状态并恢复。
 *
 * 后端成功时返回 HTTP 204，没有响应正文。
 * 恢复保留当前正文和版本号。
 * 请求失败时继续抛出，由后续 Hook 和页面处理。
 */
export function restoreKnowledgeDocument(
  // 当前登录账号的 JWT 本身，不包含 Bearer 前缀。
  accessToken: string,

  // 准备恢复的知识文档 ID。
  documentId: string,

  // 可选信号，用于停止浏览器等待。
  // 停止等待不代表撤销后端已经执行的恢复。
  signal?: AbortSignal,
): Promise<void> {
  return requestNoContent(
    // 将文档 ID 编码成适合放入 URL 路径的文字。
    `/v1/knowledge/documents/${encodeURIComponent(documentId)}/restore`,
    {
      // 恢复会改变文档状态，因此使用 POST。
      method: 'POST',

      headers: {
        // 后端通过 JWT 检查身份和操作权限。
        Authorization: `Bearer ${accessToken}`,
      },

      // 文档 ID 已包含在路径中，这个接口不需要请求体。
      signal,
    },
  )
}

/** 查询一篇文档的历史版本需要的参数。 */
type ListKnowledgeDocumentVersionsOptions = {
    // 当前登录账号的 JWT。
    accessToken: string

    // 知识文档 ID，不是入库任务 ID。
    documentId: string

    // 每页最多返回多少条，省略时使用 20。
    limit?: number

    // 跳过多少条，省略时使用 0。
    offset?: number

    // 可选信号，用于停止浏览器等待。
    signal?: AbortSignal
}

/** 分页查询历史版本摘要；请求失败时交给调用方处理。 */
export function listKnowledgeDocumentVersions(
    // 令牌、文档 ID、分页与停止等待信号。
    options: ListKnowledgeDocumentVersionsOptions,
): Promise<KnowledgeDocumentVersionListResponse> {
    // 沿用文档列表已经使用过的分页参数写法。
    const query = new URLSearchParams({
        limit: String(options.limit ?? 20),
        offset: String(options.offset ?? 0),
    })

    return requestJson<KnowledgeDocumentVersionListResponse>(
        // 编码文档 ID，再把分页参数放在问号后面。
        `/v1/knowledge/documents/${encodeURIComponent(options.documentId)}/versions?${query.toString()}`,
        {
            method: 'GET',

            headers: {
                Authorization: `Bearer ${options.accessToken}`,
            },

            signal: options.signal,
        },
    )
}

/** 查询指定文档的一个历史版本，返回摘要与当时的正文。 */
export function getKnowledgeDocumentVersion(
    // 当前登录账号的 JWT。
    accessToken: string,

    // 知识文档 ID。
    documentId: string,

    // 准备查看的历史版本号，例如 1 或 2。
    version: number,

    // 可选信号，用于停止浏览器等待请求。
    signal?: AbortSignal,
): Promise<KnowledgeDocumentVersionDetailResponse> {
    return requestJson<KnowledgeDocumentVersionDetailResponse>(
        `/v1/knowledge/documents/${encodeURIComponent(documentId)}/versions/${version}`,
        {
            method: 'GET',

            headers: {
                Authorization: `Bearer ${accessToken}`,
            },

            signal,
        },
    )
}

/** 恢复历史版本所需的调用参数。 */
type RestoreKnowledgeDocumentVersionOptions = {
    // 当前登录账号的 JWT，不包含 Bearer 前缀。
    accessToken: string

    // 准备恢复内容的文档 ID。
    documentId: string

    // 准备使用的历史版本号。
    version: number

    // 请求体：携带操作前看到的当前版本号。
    data: KnowledgeDocumentVersionRestoreRequest

    // 可选的停止等待信号。
    signal?: AbortSignal
}

/**
 * 将指定历史版本的内容恢复为新的当前版本。
 *
 * 供后续操作 Hook 调用。
 * 本函数负责发送请求并返回文档摘要，
 * 页面提示和查询缓存刷新由调用方处理。
 */
export function restoreKnowledgeDocumentVersion(
    options: RestoreKnowledgeDocumentVersionOptions,
): Promise<KnowledgeDocumentResponse> {
    return requestJson<KnowledgeDocumentResponse>(
        // 文档 ID 需要编码；version 是历史版本号。
        `/v1/knowledge/documents/${encodeURIComponent(options.documentId)}/versions/${options.version}/restore`,
        {
            method: 'POST',

            headers: {
                Authorization: `Bearer ${options.accessToken}`,
                'Content-Type': 'application/json',
            },

            // 只提交后端允许的字段。
            // 操作人身份由后端从 JWT 中读取。
            body: JSON.stringify({
                expected_version: options.data.expected_version,
            }),

            signal: options.signal,
        },
    )
}

/** 修改标题需要的令牌、文档、数据和取消信号。 */
type UpdateKnowledgeDocumentTitleOptions = {
    // 当前账号的 JWT，不包含 Bearer 前缀。
    accessToken: string

    // 准备修改的文档 ID。
    documentId: string

    // 新标题和编辑时取得的当前版本号。
    data: KnowledgeDocumentTitleUpdateRequest

    // 可选的停止等待信号。
    signal?: AbortSignal
}

/** 保存新标题，成功时返回版本号已递增的文档摘要。 */
export function updateKnowledgeDocumentTitle(
    options: UpdateKnowledgeDocumentTitleOptions,
): Promise<KnowledgeDocumentResponse> {
    return requestJson<KnowledgeDocumentResponse>(
        '/v1/knowledge/documents/' +
        encodeURIComponent(options.documentId) +
        '/title',
        {
            method: 'PATCH',

            headers: {
                Authorization: 'Bearer ' + options.accessToken,
                'Content-Type': 'application/json',
            },

            // 只发送这个接口允许修改的字段。
            body: JSON.stringify({
                title: options.data.title,
                expected_version: options.data.expected_version,
            }),

            signal: options.signal,
        },
    )
}