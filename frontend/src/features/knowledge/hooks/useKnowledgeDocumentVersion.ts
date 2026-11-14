/**
 * 文件职责：管理单个历史版本的详情查询、缓存和刷新。
 * 作用与配合：读取登录身份，调用 getKnowledgeDocumentVersion，提供查询结果。
 * 使用位置：供后续历史版本详情页面调用。
 * 边界：不绘制页面、不修改文档或恢复版本；权限由后端再次检查。
 */
import {useQuery} from '@tanstack/react-query'

import {useAuth} from '../../auth/hooks/useAuth'
import {getKnowledgeDocumentVersion} from '../api'

/** 按文档 ID 和版本号查询一份历史正文。 */
export function useKnowledgeDocumentVersion(
    // 尚未取得路由参数时，允许为空。
    documentId: string | undefined,

    // 转换成数字后的历史版本号；缺失时允许为空。
    version: number | undefined,
) {
    const {session, status: authStatus} = useAuth()

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    const hasValidDocumentId =
        typeof documentId === 'string' &&
        documentId.length >= 1 &&
        documentId.length <= 36

    // 版本号必须是 JavaScript 能精确表示的正整数。
    const hasValidVersion =
        typeof version === 'number' &&
        Number.isSafeInteger(version) &&
        version >= 1

    const query = useQuery({
        // 同一篇文档的不同历史版本也分别保存缓存。
        queryKey: [
            'knowledge-documents',
            session?.user.id ?? null,
            session?.user.role ?? null,
            'version-detail',
            documentId ?? null,
            version ?? null,
        ],

        enabled: canManage && hasValidDocumentId && hasValidVersion,

        queryFn: ({signal}) => {
            // 手动刷新也会执行查询函数，因此再次检查。
            if (
                !session || !canManage ||
                !hasValidDocumentId || !documentId ||
                !hasValidVersion || version === undefined
            ) {
                throw new Error('当前身份、文档 ID 或版本号不允许查询历史正文')
            }

            return getKnowledgeDocumentVersion(
                session.accessToken,
                documentId,
                version,
                signal,
            )
        },

        // 不自动重试，也不定时轮询历史正文。
        retry: false,
        refetchInterval: false,

        // 切回页面时补查；已有错误时等待手动刷新。
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',

        refetchOnReconnect: false,

        // 不借用其他文档或其他版本的正文作为临时内容。
    })

    return {canManage, hasValidDocumentId, hasValidVersion, query}
}