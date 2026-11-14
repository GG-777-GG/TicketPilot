/**
 * 文件职责：管理单篇知识文档的历史版本分页查询与缓存。
 * 作用与配合：读取登录身份，调用 listKnowledgeDocumentVersions，提供查询结果。
 * 使用位置：供后续历史版本列表组件调用。
 * 边界：不绘制页面、不保存分页状态、不修改或恢复历史版本；权限由后端再次检查。
 */
import {useQuery} from '@tanstack/react-query'

import {useAuth} from '../../auth/hooks/useAuth'
import {listKnowledgeDocumentVersions} from '../api'

/** 页面交给历史版本查询 Hook 的分页参数。 */
type UseKnowledgeDocumentVersionsOptions = {
    // 每页最多返回多少条，默认 10。
    limit?: number

    // 跳过多少条，默认 0。
    offset?: number
}

/** 查询指定文档的历史版本，向组件提供权限、ID 检查与查询状态。 */
export function useKnowledgeDocumentVersions(
    // 准备查询历史记录的知识文档 ID。
    documentId: string | undefined,

    // 省略分页参数时，从第一页开始查询。
    options: UseKnowledgeDocumentVersionsOptions = {},
) {
    const {session, status: authStatus} = useAuth()

    // 分页状态由页面保存，这里读取传入值。
    const limit = options.limit ?? 10
    const offset = options.offset ?? 0

    // 只有身份已确认的主管或管理员才允许查询。
    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // 与后端一样检查 ID 长度，不代表文档一定存在。
    const hasValidDocumentId =
        typeof documentId === 'string' &&
        documentId.length >= 1 &&
        documentId.length <= 36

    const query = useQuery({
        // 每个账号、角色、文档和分页组合分别保存缓存。
        // versions 区分历史列表与当前文档详情，JWT 不放进缓存键。
        queryKey: [
            'knowledge-documents',
            session?.user.id ?? null,
            session?.user.role ?? null,
            'versions',
            documentId ?? null,
            {limit, offset},
        ],

        enabled: canManage && hasValidDocumentId,

        // signal 由 Query 提供，传给 API 以停止不再需要的请求等待。
        queryFn: ({signal}) => {
            // 手动 refetch 也会执行这里，因此再次检查。
            if (
                !session ||
                !canManage ||
                !hasValidDocumentId ||
                !documentId
            ) {
                throw new Error('当前身份或文档 ID 不允许查询历史版本')
            }

            return listKnowledgeDocumentVersions({
                accessToken: session.accessToken,
                documentId,
                limit,
                offset,
                signal,
            })
        },

        // 失败后不自动重试，也不定时轮询。
        retry: false,
        refetchInterval: false,

        // 切回页面时补查；已有错误时等待用户手动刷新。
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',

        refetchOnReconnect: false,

        // 不设置 placeholderData，切换文档或翻页时不借用旧记录。
    })

    return {canManage, hasValidDocumentId, query}
}