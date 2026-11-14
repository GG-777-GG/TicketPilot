/**
 * 文件职责：查询指定工单的详情，管理查询状态与缓存。
 * 作用与配合：读取登录身份，调用已有的 getTicket。
 * 使用位置：由 TicketPage 调用。
 * 边界：只读取工单，不修改状态、处理人或正文。
 */
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '../../auth/hooks/useAuth'
import { getTicket } from '../api'

/** ticketId 来自页面地址，未提供时不发送请求。 */
export function useTicket(ticketId: string | undefined) {
    const { session, status } = useAuth()
    const canRead = status === 'authenticated' && session !== null
    const hasTicketId = Boolean(ticketId)

    const query = useQuery({
        // 不同账号、角色、工单分别使用自己的查询缓存。
        queryKey: [
            'ticket-detail',
            session?.user.id ?? null,
            session?.user.role ?? null,
            ticketId ?? null,
        ],

        enabled: canRead && hasTicketId,

        queryFn: ({ signal }) => {
            // 手动刷新也会执行这里，因此再次检查参数。
            if (!session || !canRead || !ticketId) {
                throw new Error('缺少登录身份或工单 ID')
            }

            return getTicket(session.accessToken, ticketId, signal)
        },

        retry: false,
        refetchInterval: false,
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',
        refetchOnReconnect: false,
    })

    return { canRead, hasTicketId, query }
}