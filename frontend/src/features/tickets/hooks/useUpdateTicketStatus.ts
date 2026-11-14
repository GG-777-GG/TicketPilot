/**
 * 文件职责：管理工单状态修改、等待状态和结果提示。
 * 作用与配合：调用状态修改 API，并刷新当前账号的工单缓存。
 * 使用位置：供下一批的工单状态编辑组件调用。
 * 边界：不绘制表单、不自动提交或重试；取消等待不撤销修改。
 */
import {useLayoutEffect, useRef, useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'

import {ApiError} from '../../../shared/api/http'
import { invalidateTicketQueries } from '../cache'
import {useAuth} from '../../auth/hooks/useAuth'
import {updateTicketStatus} from '../api'
import type {TicketStatus} from '../types'

// 同时用于运行时检查，避免只依赖 TypeScript 类型。
const VALID_STATUSES: TicketStatus[] = [
    'open',
    'in_progress',
    'resolved',
    'closed',
]

/** 根据错误类型生成保存提示。 */
function getSaveMessage(error: unknown): string {
    if (error instanceof ApiError) {
        switch (error.status) {
            case 401:
                return '登录已失效，请重新登录。'
            case 403:
                return '当前账号没有修改工单状态的权限。'
            case 404:
                return '工单不存在或已被删除，请返回列表。'
            case 409:
                return '当前操作不符合工单规则；关闭工单需要提交审批申请，并在获批后执行。'
            case 422:
                return '提交的状态不符合要求，请检查后再操作。'
        }
    }

    // 网络断开或服务异常时，后端可能已经完成了修改。
    return '暂时无法确认保存结果，请刷新详情核对状态后再操作。'
}

/** ticketId 是当前准备修改的工单 ID。 */
export function useUpdateTicketStatus(ticketId: string | undefined) {
    const {session, status: authStatus} = useAuth()
    const queryClient = useQueryClient()

    const [isSaving, setIsSaving] = useState(false)
    const [saveMessage, setSaveMessage] = useState('')
    // 保存请求的 HTTP 状态码，供页面识别登录失效等情况。
    const [saveHttpStatus, setSaveHttpStatus] = useState<number | undefined>()
    const requestRef = useRef<AbortController | null>(null)

    const canUpdate =
        authStatus === 'authenticated' &&
        session !== null &&
        Boolean(ticketId)

    // 工作台的 key 负责重置保存状态；这里不再接收旧请求结果。
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

    /** 提交目标状态；true 表示已确认保存，false 表示未确认成功。 */
    async function updateStatus(nextStatus: TicketStatus): Promise<boolean> {
        if (requestRef.current) return false

        if (!session || !canUpdate || !ticketId) {
            setSaveMessage('请先登录并打开有效的工单。')
            return false
        }

        if (!VALID_STATUSES.includes(nextStatus)) {
            setSaveMessage('请选择有效的工单状态。')
            return false
        }

        // closed 仍是合法的展示状态，但不能通过普通状态接口提交。
        if (nextStatus === 'closed') {
            setSaveHttpStatus(undefined)
            setSaveMessage('关闭工单需要提交审批申请，并在获批后执行。')
            return false
        }

        const actorId = session.user.id
        const actorRole = session.user.role
        const controller = new AbortController()

        requestRef.current = controller
        setIsSaving(true)
        setSaveMessage('')
        setSaveHttpStatus(undefined)

        try {
            await updateTicketStatus({
                accessToken: session.accessToken,
                ticketId,
                status: nextStatus,
                signal: controller.signal,
            })

            // 身份或工单已切换时，不再接收旧请求的页面结果。
            if (requestRef.current !== controller) return false

            setSaveMessage('状态已保存。')
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
                // 刷新本次操作者的工单列表与详情缓存。
                await invalidateTicketQueries(queryClient, actorId, actorRole, 'all')
                // 刷新异常不改变已经确认的保存结果。
                // 详情查询自身仍负责展示读取错误。
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
        updateStatus,
    }
}
