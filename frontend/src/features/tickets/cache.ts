/**
 * 文件职责：集中处理工单写操作后的查询缓存刷新。
 * 作用与配合：供创建、状态修改、处理人分配和删除 Hook 调用。
 * 使用位置：工单写操作结束后的 finally 阶段。
 * 边界：只刷新指定账号与角色的缓存，不发送写请求或修改操作结果。
 */
import type { QueryClient } from '@tanstack/react-query'
import type { UserRole } from '../auth/types'

/** 省略时只刷新列表；all 刷新全部工单详情；对象指定单张详情。 */
type DetailScope = 'all' | { ticketId: string }

/** 刷新操作者的列表及指定详情；刷新失败不覆盖已确认的写操作结果。 */
export async function invalidateTicketQueries(
    queryClient: QueryClient,
    actorId: string,
    actorRole: UserRole,
    details?: DetailScope,
): Promise<void> {
    await queryClient.invalidateQueries({
        predicate: (query) => {
            const key = query.queryKey
            if (key[1] !== actorId || key[2] !== actorRole) return false
            if (key[0] === 'tickets') return true
            if (key[0] !== 'ticket-detail' || !details) return false
            return details === 'all' || key[3] === details.ticketId
        },
    }).catch(() => undefined)
}
