/**
 * 文件职责：管理工单创建、等待状态和创建结果。
 * 作用与配合：调用创建 API，并刷新当前账号的工单列表缓存。
 * 使用位置：供后续创建工单页面调用。
 * 边界：不绘制表单、不自动重试；不能提供后端幂等保证。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { invalidateTicketQueries } from '../cache'
import { useAuth } from '../../auth/hooks/useAuth'
import { createTicket } from '../api'
import type { TicketCreateRequest, TicketResponse } from '../types'

/** 提供一次创建流程；成功或结果不确定后暂停本页重复提交。 */
export function useCreateTicket() {
    const { session, status: authStatus } = useAuth()
    const queryClient = useQueryClient()

    const [isCreating, setIsCreating] = useState(false)
    const [createMessage, setCreateMessage] = useState('')
    const [createHttpStatus, setCreateHttpStatus] =
        useState<number | undefined>()
    const [createdTicket, setCreatedTicket] =
        useState<TicketResponse | null>(null)
    const [needsCheck, setNeedsCheck] = useState(false)

    const requestRef = useRef<AbortController | null>(null)

    // 除了拦住并发请求，还阻止成功或结果不确定后的重复提交。
    const blockedRef = useRef(false)

    const canCreate =
        authStatus === 'authenticated' &&
        session !== null &&
        !isCreating &&
        createdTicket === null &&
        !needsCheck &&
        createHttpStatus !== 401 &&
        createHttpStatus !== 403

    // 页面重建时状态和 ref 自动初始化；离开页面只停止等待请求。
    useLayoutEffect(() => {
        return () => {
            const controller = requestRef.current
            requestRef.current = null
            controller?.abort()
        }
    }, [
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])

    /** 提交表单数据；确认成功时返回新工单，其余返回 undefined。 */
    async function submitTicket(
        data: TicketCreateRequest,
    ): Promise<TicketResponse | undefined> {
        if (requestRef.current || blockedRef.current) return undefined

        if (!session || authStatus !== 'authenticated') {
            setCreateMessage('请先登录再创建工单。')
            return undefined
        }

        // 文本先去首尾空白；字段合法性仍由后端最终校验。
        const normalized: TicketCreateRequest = {
            ...data,
            customer_name: data.customer_name.trim(),
            contact_email: data.contact_email.trim(),
            subject: data.subject.trim(),
            description: data.description.trim(),
        }

        const actorId = session.user.id
        const actorRole = session.user.role
        const controller = new AbortController()

        requestRef.current = controller
        blockedRef.current = true
        setIsCreating(true)
        setCreateMessage('')
        setCreateHttpStatus(undefined)

        let created: TicketResponse | undefined

        try {
            const ticket = await createTicket({
                accessToken: session.accessToken,
                data: normalized,
                signal: controller.signal,
            })

            if (requestRef.current !== controller) return undefined

            setCreatedTicket(ticket)
            setCreateMessage('工单已创建。')
            created = ticket
        } catch (error) {
            if (requestRef.current !== controller) return undefined

            const code =
                error instanceof ApiError ? error.status : undefined

            setCreateHttpStatus(code)

            if (code === 422) {
                // 校验未通过，可以修改表单后重新提交。
                blockedRef.current = false
                setCreateMessage(
                    '提交内容不符合要求，请检查各字段后再提交。',
                )
            } else if (code === 401 || code === 403) {
                setCreateMessage(
                    code === 401
                        ? '登录已失效，请重新登录。'
                        : '当前账号没有创建工单的权限。',
                )
            } else {
                // 可能已创建：保持阻止提交，先由用户去列表核对。
                setNeedsCheck(true)
                setCreateMessage(
                    '暂时无法确认是否创建成功，请先到工单列表核对，避免重复创建。',
                )
            }

            return undefined
        } finally {
            try {
                await invalidateTicketQueries(queryClient, actorId, actorRole)
            } finally {
                if (requestRef.current === controller) {
                    requestRef.current = null
                    setIsCreating(false)
                }
            }
        }

        // 身份可能在刷新列表期间变化，返回前再检查一次取消信号。
        return controller.signal.aborted ? undefined : created
    }

    return {
        canCreate,
        isCreating,
        createMessage,
        createHttpStatus,
        createdTicket,
        needsCheck,
        submitTicket,
    }
}
