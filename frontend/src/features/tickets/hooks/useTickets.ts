/**
 * 文件职责：管理工单列表的查询、分页数据和缓存。
 * 作用与配合：读取登录身份，调用 listTickets。
 * 使用位置：由 TicketsPage 的列表组件调用。
 * 边界：不绘制页面，不创建或修改工单，不计算全库总数。
 */
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '../../auth/hooks/useAuth'
import { listTickets } from '../api'

// 页面展示 10 条，请求时额外取 1 条判断是否还有下一页。
export const TICKET_PAGE_SIZE = 10

/** 查询指定页；pageIndex 从 0 开始，由页面维护非负整数。 */
export function useTickets(pageIndex = 0) {
    const { session, status: authStatus } = useAuth()
    const canRead = authStatus === 'authenticated' && session !== null
    const limit = TICKET_PAGE_SIZE + 1
    const offset = pageIndex * TICKET_PAGE_SIZE

    const query = useQuery({
        // 账号、角色或分页变化时，使用对应的独立缓存。
        queryKey: [
            'tickets',
            session?.user.id ?? null,
            session?.user.role ?? null,
            { limit, offset },
        ],
        enabled: canRead,
        queryFn: ({ signal }) => {
            // 手动 refetch 也会调用这里，因此再次检查身份。
            if (!session || !canRead) {
                throw new Error('请先登录再查询工单')
            }

            return listTickets({
                accessToken: session.accessToken,
                limit,
                offset,
                signal,
            })
        },

        // select 将接口数组转换成页面需要的结构。
        select: (tickets) => ({
            items: tickets.slice(0, TICKET_PAGE_SIZE),
            hasNext: tickets.length > TICKET_PAGE_SIZE,
        }),

        // 翻页时等待对应页的数据，不借用上一页内容。
        retry: false,
        refetchInterval: false,
        refetchOnWindowFocus: (currentQuery) =>
            currentQuery.state.status !== 'error',
        refetchOnReconnect: false,
    })

    return { canRead, query }
}
