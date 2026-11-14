/**
 * 文件职责：按文档 ID 管理单篇知识文档的查询、缓存和刷新。
 * 作用与配合：读取登录身份，调用 getKnowledgeDocument，提供查询结果。
 * 使用位置：供后续知识文档详情页面调用。
 * 边界：不绘制页面、不修改文档；实际访问权限由后端再次检查。
 */

import {useQuery} from '@tanstack/react-query'

import {useAuth} from '../../auth/hooks/useAuth'
import {getKnowledgeDocument} from '../api'

/**
 * 查询一篇知识文档，并向页面提供权限、ID 检查和查询状态。
 *
 * 页面取得文档 ID 且身份符合要求后，才自动发起请求。
 */
export function useKnowledgeDocument(
    // 准备查看的文档 ID。
    // 页面尚未取得路由参数时，允许是 undefined。
    documentId: string | undefined,
) {
    const {session, status: authStatus} = useAuth()

    // 身份确认后，主管和管理员可以查询文档详情。
    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // typeof 检查是不是字符串，length 获取字符串长度。
    // 与后端一样检查长度；这不代表文档一定存在。
    const hasValidDocumentId =
        typeof documentId === 'string' &&
        documentId.length >= 1 &&
        documentId.length <= 36

    const query = useQuery({
        // 不同账号、角色和文档分别保存缓存。
        // detail 用来区分详情查询与列表查询。
        queryKey: [
            'knowledge-documents',
            session?.user.id ?? null,
            session?.user.role ?? null,
            'detail',
            documentId ?? null,
        ],

        // enabled 决定是否自动发起请求。
        // && 表示左右两个条件都必须成立。
        enabled: canManage && hasValidDocumentId,

        // signal 由 Query 提供，用于停止等待不再需要的请求。
        queryFn: ({signal}) => {
            // 手动 refetch 也可能执行这里，所以再次检查。
            if (
                !session ||
                !canManage ||
                !hasValidDocumentId ||
                !documentId
            ) {
                throw new Error('当前身份或文档 ID 不允许查询文档详情')
            }

            return getKnowledgeDocument(
                session.accessToken,
                documentId,
                signal,
            )
        },

        // 请求失败后不自动重试，由页面提供手动刷新入口。
        retry: false,

        // 文档详情不定时轮询，任务进度由入库任务页面负责。
        refetchInterval: false,

        // 切回页面时补查一次；已有错误时等待手动刷新。
        // currentQuery 是 Query 管理的本次查询对象。
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',

        // 网络恢复时，不额外自动查询。
        refetchOnReconnect: false,

        // 不设置 placeholderData：
        // 切换文档时，不借用上一篇文档的正文作为临时结果。
    })

    // 页面根据这些信息，决定显示正文、加载或错误提示。
    return {canManage, hasValidDocumentId, query}
}
