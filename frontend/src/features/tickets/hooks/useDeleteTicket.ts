/**
 * 文件职责：管理确认后的工单删除、等待状态及结果。
 * 作用与配合：调用删除 API，刷新当前账号的列表和目标详情。
 * 使用位置：供下一批的删除确认区调用。
 * 边界：不绘制确认界面，不自动删除或重试；停止等待不撤销删除。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { invalidateTicketQueries } from '../cache'
import { useAuth } from '../../auth/hooks/useAuth'
import { deleteTicket } from '../api'

type DeleteOutcome =
    | 'idle'
    | 'deleted'
    | 'missing'
    | 'rejected'
    | 'unknown'

/** ticketId 是当前详情页对应的工单 ID。 */
export function useDeleteTicket(ticketId: string | undefined) {
    const { session, status: authStatus } = useAuth()
    const queryClient = useQueryClient()

    const [isDeleting, setIsDeleting] = useState(false)
    const [deleteMessage, setDeleteMessage] = useState('')
    const [deleteHttpStatus, setDeleteHttpStatus] =
        useState<number | undefined>()
    const [deleteOutcome, setDeleteOutcome] =
        useState<DeleteOutcome>('idle')

    const requestRef = useRef<AbortController | null>(null)
    const attemptedRef = useRef(false)

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    const canDelete =
        canManage &&
        Boolean(ticketId) &&
        !isDeleting &&
        deleteOutcome === 'idle'

    // 工作台的 key 负责重置操作状态；这里取消旧请求。
    useLayoutEffect(() => {
        return () => {
            const controller = requestRef.current
            requestRef.current = null
            controller?.abort()
        }
    }, [
        ticketId,
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])

    /** confirmedTicketId 必须是用户确认区中显示的那张工单。 */
    async function deleteConfirmed(
        confirmedTicketId: string,
    ): Promise<boolean> {
        if (requestRef.current || attemptedRef.current) return false

        if (!session || !canManage) {
            setDeleteMessage('只有主管或管理员可以删除工单。')
            return false
        }

        if (!ticketId || confirmedTicketId !== ticketId) {
            setDeleteMessage('待删除工单已变化，请重新打开确认区。')
            return false
        }

        const actorId = session.user.id
        const actorRole = session.user.role
        const controller = new AbortController()
        let deleted = false

        requestRef.current = controller
        attemptedRef.current = true
        setIsDeleting(true)
        setDeleteMessage('')
        setDeleteHttpStatus(undefined)

        try {
            await deleteTicket(
                session.accessToken,
                confirmedTicketId,
                controller.signal,
            )

            if (requestRef.current !== controller) return false

            deleted = true
            setDeleteOutcome('deleted')
            setDeleteMessage('工单已删除，请返回列表。')
        } catch (error) {
            if (requestRef.current !== controller) return false

            const code =
                error instanceof ApiError ? error.status : undefined

            setDeleteHttpStatus(code)

            if (code === 404) {
                setDeleteOutcome('missing')
                setDeleteMessage('工单已不存在，请返回列表。')
            } else if (code === 401 || code === 403) {
                setDeleteOutcome('rejected')
                setDeleteMessage(
                    code === 401
                        ? '登录已失效，请重新登录。'
                        : '当前账号没有删除工单的权限。',
                )
            } else {
                setDeleteOutcome('unknown')
                setDeleteMessage(
                    '暂时无法确认删除结果，请返回列表刷新并核对。',
                )
            }
        } finally {
            try {
                await invalidateTicketQueries(queryClient, actorId, actorRole, { ticketId })
            } finally {
                if (requestRef.current === controller) {
                    requestRef.current = null
                    setIsDeleting(false)
                }
            }
        }

        return !controller.signal.aborted && deleted
    }

    return {
        canManage,
        canDelete,
        isDeleting,
        deleteMessage,
        deleteHttpStatus,
        deleteOutcome,
        deleteConfirmed,
    }
}
