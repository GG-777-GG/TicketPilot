/**
 * 文件职责：管理处理人分配、取消分配及保存结果。
 * 作用与配合：调用处理人 API，并刷新操作者的工单缓存。
 * 使用位置：供后续工单处理人编辑组件调用。
 * 边界：不绘制表单，不查询用户列表，不自动重试写请求。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { invalidateTicketQueries } from '../cache'
import { useAuth } from '../../auth/hooks/useAuth'
import { updateTicketAssignee } from '../api'

/** 将保存错误转换为可显示的说明。 */
function getSaveMessage(error: unknown): string {
    if (error instanceof ApiError) {
        switch (error.status) {
            case 401:
                return '登录已失效，请重新登录。'
            case 403:
                return '当前账号没有修改处理人的权限。'
            case 404:
                return '工单不存在或已被删除，请返回列表。'
            case 422:
                return '处理人信息不符合要求，请检查后再提交。'
        }
    }

    return '暂时无法确认保存结果，请重新读取工单核对处理人后再操作。'
}

/** ticketId 是当前准备分配处理人的工单 ID。 */
export function useUpdateTicketAssignee(ticketId: string | undefined) {
    const { session, status: authStatus } = useAuth()
    const queryClient = useQueryClient()

    const [isSaving, setIsSaving] = useState(false)
    const [saveMessage, setSaveMessage] = useState('')
    const [saveHttpStatus, setSaveHttpStatus] = useState<number | undefined>()
    const requestRef = useRef<AbortController | null>(null)

    const canUpdate =
        authStatus === 'authenticated' &&
        session !== null &&
        Boolean(ticketId)

    // 工作台的 key 负责重置保存状态；这里取消旧请求。
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

    /** 字符串用于分配处理人；null 用于明确取消分配。 */
    async function updateAssignee(
        assignedTo: string | null,
    ): Promise<boolean> {
        if (requestRef.current) return false

        if (!session || !canUpdate || !ticketId) {
            setSaveMessage('请先登录并打开有效的工单。')
            return false
        }

        // 空字符串不自动当作取消分配，避免误操作。
        const normalized =
            assignedTo === null ? null : assignedTo.trim()

        if (normalized !== null) {
            const length = Array.from(normalized).length

            if (length < 1 || length > 100) {
                setSaveMessage('处理人不能为空，且不能超过 100 个字符。')
                return false
            }
        }

        const actorId = session.user.id
        const actorRole = session.user.role
        const controller = new AbortController()

        requestRef.current = controller
        setIsSaving(true)
        setSaveMessage('')
        setSaveHttpStatus(undefined)

        try {
            await updateTicketAssignee({
                accessToken: session.accessToken,
                ticketId,
                assignedTo: normalized,
                signal: controller.signal,
            })

            if (requestRef.current !== controller) return false

            setSaveMessage(
                normalized === null
                    ? '已取消分配处理人。'
                    : '处理人已保存。',
            )
            return true
        } catch (error) {
            if (requestRef.current !== controller) return false

            setSaveHttpStatus(
                error instanceof ApiError ? error.status : undefined,
            )
            setSaveMessage(getSaveMessage(error))
            return false
        } finally {
            try {
                await invalidateTicketQueries(queryClient, actorId, actorRole, 'all')
            } finally {
                if (requestRef.current === controller) {
                    requestRef.current = null
                    setIsSaving(false)
                }
            }
        }
    }

    return {
        canUpdate,
        isSaving,
        saveMessage,
        saveHttpStatus,
        updateAssignee,
    }
}
