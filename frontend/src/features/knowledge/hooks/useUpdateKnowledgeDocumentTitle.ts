/**
 * 文件职责：管理知识文档标题的保存、等待状态与结果提示。
 * 作用与配合：调用标题 API，通知 Query 刷新操作者的文档缓存。
 * 使用位置：供后续文档标题编辑组件调用。
 * 边界：不绘制输入框，不自动保存或重试；取消等待不撤销后端写入。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { updateKnowledgeDocumentTitle } from '../api'
import type { KnowledgeDocumentResponse } from '../types'

/** 点击保存时固定下来的请求参数。 */
type UpdateTitleVariables = {
    accessToken: string
    documentId: string
    title: string
    expectedVersion: number
    signal: AbortSignal
}

/** 区分确定的 HTTP 错误和无法确认的保存结果。 */
function getSaveMessage(error: unknown): string {
    if (error instanceof ApiError) {
        switch (error.status) {
            case 401:
                return '登录已失效，请重新登录。'

            case 403:
                return '当前账号没有修改标题的权限。'

            case 404:
                return '文档不存在，请返回文档列表。'

            case 409:
                // 标题未变、版本过期、状态不允许，都可能返回 409。
                // 保留后端的具体原因。
                return error.message +
                    '。请刷新当前文档，核对标题和版本后再操作。'

            case 422:
                return '标题或版本号不符合要求，请核对后再提交。'

            default:
                if (error.status < 500) return error.message
        }
    }

    return '暂时无法确认标题是否保存，请先刷新当前文档核对结果。'
}

/** 只有调用 updateTitle 才发送保存请求。 */
export function useUpdateKnowledgeDocumentTitle(
    // 当前准备修改的文档 ID。
    documentId: string | undefined,
) {
    const { session, status: authStatus } = useAuth()
    const queryClient = useQueryClient()

    const [isSaving, setIsSaving] = useState(false)
    const [saveMessage, setSaveMessage] = useState('')
    const requestRef = useRef<AbortController | null>(null)

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // 切换文档或身份后，不保留旧保存状态，也不接收旧结果。
    useLayoutEffect(() => {
        return () => {
            const controller = requestRef.current
            requestRef.current = null
            controller?.abort()
        }
    }, [
        documentId,
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])

    const mutation = useMutation({
        mutationFn: (variables: UpdateTitleVariables) => {
            variables.signal.throwIfAborted()

            return updateKnowledgeDocumentTitle({
                accessToken: variables.accessToken,
                documentId: variables.documentId,
                data: {
                    title: variables.title,
                    expected_version: variables.expectedVersion,
                },
                signal: variables.signal,
            })
        },

        retry: false,
        networkMode: 'always',
    })

    /** 校验并保存标题；有效的成功请求返回新摘要，其余返回 undefined。 */
    async function updateTitle(
        // 用户输入的标题；请求使用去掉首尾空白后的值。
        title: string,

        // 开始编辑时取得的当前版本，
        // 不能自动换成后来更新的版本。
        expectedVersion: number,
    ): Promise<KnowledgeDocumentResponse | undefined> {
        if (requestRef.current) return undefined

        if (!session || !canManage) {
            setSaveMessage('当前身份不能修改文档标题。')
            return undefined
        }

        if (!documentId || documentId.length > 36) {
            setSaveMessage('文档 ID 无效，请返回文档列表。')
            return undefined
        }

        const normalizedTitle = title.trim()

        // 按 Unicode 码点计数，与后端的标题长度检查对应。
        const titleLength = Array.from(normalizedTitle).length

        if (titleLength < 1 || titleLength > 200) {
            setSaveMessage('标题不能为空，且不能超过 200 个字符。')
            return undefined
        }

        if (
            !Number.isSafeInteger(expectedVersion) ||
            expectedVersion < 1
        ) {
            setSaveMessage('当前版本号无效，请刷新文档后重新编辑。')
            return undefined
        }

        const actorId = session.user.id
        const controller = new AbortController()

        requestRef.current = controller
        setIsSaving(true)
        setSaveMessage('')

        try {
            const result = await mutation.mutateAsync({
                accessToken: session.accessToken,
                documentId,
                title: normalizedTitle,
                expectedVersion,
                signal: controller.signal,
            })

            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return undefined

            setSaveMessage(
                '标题已保存，当前版本为 v' + result.version + '。',
            )

            return result
        } catch (error) {
            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return undefined

            setSaveMessage(getSaveMessage(error))
            return undefined
        } finally {
            // 标记列表、当前详情和历史版本缓存为过期。
            // 查询刷新失败不改变本次写请求的成功或失败结果。
            void queryClient.invalidateQueries({
                queryKey: ['knowledge-documents', actorId],
            }).catch(() => undefined)

            if (requestRef.current === controller) {
                requestRef.current = null

                if (!controller.signal.aborted) {
                    setIsSaving(false)
                }
            }
        }
    }

    return { updateTitle, isSaving, saveMessage }
}