/**
 * 文件职责：管理知识文档列表的分页查询、缓存和加载状态。
 * 作用与配合：读取登录身份，调用 listKnowledgeDocuments，向页面提供查询结果。
 * 使用位置：供后续知识文档列表页面调用。
 * 边界：不绘制页面、不上传或归档文档；查看权限由后端再次校验。
 */

import {useQuery} from '@tanstack/react-query'

import {useAuth} from '../../auth/hooks/useAuth'
import {listKnowledgeDocuments} from '../api'

/** 页面交给查询 Hook 的分页参数。 */
type UseKnowledgeDocumentsOptions = {
    // 每页数量，前端默认 10 篇；后端允许 1～100。
    limit?: number

    // 跳过多少篇文档；默认 0，表示从第一页开始。
    offset?: number
}

/** 查询当前账号有权管理的文档列表，并提供手动刷新能力。 */
export function useKnowledgeDocuments(
    // 默认空对象，允许页面直接调用 useKnowledgeDocuments()。
    options: UseKnowledgeDocumentsOptions = {},
) {
    const {session, status: authStatus} = useAuth()

    // 页面主动指定分页数量，这里的默认 10 会传给 API。
    const limit = options.limit ?? 10
    const offset = options.offset ?? 0

    // 只有身份已确认的主管或管理员才允许开始查询。
    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    const query = useQuery({
        // 按账号、角色和分页参数区分缓存。
        // JWT 不放进缓存键。
        queryKey: [
            'knowledge-documents',
            session?.user.id ?? null,
            session?.user.role ?? null,
            {limit, offset},
        ],

        enabled: canManage,

        // signal 由 Query 提供，用于停止等待不再需要的请求。
        queryFn: ({signal}) => {
            // 手动 refetch 也会执行这里，因此再次检查身份。
            if (!session || !canManage) {
                throw new Error('当前身份不能查询知识文档列表')
            }

            return listKnowledgeDocuments({
                accessToken: session.accessToken,
                limit,
                offset,
                signal,
            })
        },

        // 翻页时暂时展示上一页，减少页面闪空。
        // 只允许借用同一账号、角色和每页数量下的数据。
        placeholderData: (previousData, previousQuery) => {
            const previousKey = previousQuery?.queryKey
            const previousOptions = previousKey?.[3]

            if (
                !canManage ||
                previousKey?.[0] !== 'knowledge-documents' ||
                previousKey?.[1] !== session?.user.id ||
                previousKey?.[2] !== session?.user.role ||
                !previousOptions ||
                typeof previousOptions !== 'object' ||
                !('limit' in previousOptions) ||
                previousOptions.limit !== limit
            ) {
                return undefined
            }

            return previousData
        },

        // 查询失败后不自动重试，由页面提供手动刷新入口。
        retry: false,

        // 文档列表暂不定时轮询。
        // 任务处理进度继续由入库任务页面查询。
        refetchInterval: false,

        // 切回页面时补查一次。
        // 已有查询错误时，等待用户手动刷新。
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',

        // 网络恢复时，不额外触发刷新。
        refetchOnReconnect: false,
    })

    return {canManage, query}
}