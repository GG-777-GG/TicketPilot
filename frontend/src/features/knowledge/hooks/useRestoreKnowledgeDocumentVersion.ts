/**
 * 文件职责：管理历史版本恢复请求、提交状态与结果提示。
 * 作用与配合：调用恢复 API，标记操作者的知识文档缓存为过期。
 * 使用位置：供历史版本详情页的确认操作调用。
 * 边界：不绘制按钮或确认界面，不自动重发；取消等待不会撤销后端写入。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { restoreKnowledgeDocumentVersion } from '../api'
import type { KnowledgeDocumentResponse } from '../types'

/** 提交时固定下来的请求参数，后续渲染不改变这份快照。 */
type RestoreVersionVariables = {
    accessToken: string
    documentId: string
    version: number
    expectedVersion: number
    signal: AbortSignal
}

/** 将请求错误转换为页面提示；结果不确定时要求先查询。 */
function getRestoreMessage(error: unknown): string {
    if (error instanceof ApiError) {
        switch (error.status) {
            case 401: return '登录已失效，请重新登录。'
            case 403: return '当前账号没有恢复历史版本的权限。'
            case 404: return '文档或历史版本不存在，请返回当前文档核对。'
            case 409:
                return '版本、文档状态或内容存在冲突，请先刷新当前文档再重新确认。'
            case 422: return '恢复参数不正确，请刷新页面后核对版本信息。'
            default:
                if (error.status < 500) return error.message
        }
    }

    return '暂时无法确认恢复结果，请先刷新当前文档，核对版本和内容后再操作。'
}

/** 提供恢复方法、等待状态与提示；仅调用 restoreVersion 时提交。 */
export function useRestoreKnowledgeDocumentVersion(
    // 当前页面对应的文档。
    documentId: string | undefined,

    // 准备恢复内容的历史版本，不是当前版本。
    version: number | undefined,
) {
    const { session, status: authStatus } = useAuth()
    const queryClient = useQueryClient()

    const [isRestoring, setIsRestoring] = useState(false)
    const [restoreMessage, setRestoreMessage] = useState('')

    // ref 立即上锁，state 负责让按钮显示等待状态。
    const requestRef = useRef<AbortController | null>(null)

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // key 负责重置等待状态和提示；这里只清理旧请求。
    useLayoutEffect(() => {
        return () => {
            const controller = requestRef.current
            requestRef.current = null
            controller?.abort()
        }
    }, [
        documentId, version, authStatus,
        session?.user.id, session?.user.role, session?.accessToken,
    ])

    const mutation = useMutation({
        // 使用点击时传入的参数，不读取后来变化的登录信息。
        mutationFn: (variables: RestoreVersionVariables) => {
            variables.signal.throwIfAborted()

            return restoreKnowledgeDocumentVersion({
                accessToken: variables.accessToken,
                documentId: variables.documentId,
                version: variables.version,
                data: {
                    expected_version: variables.expectedVersion,
                },
                signal: variables.signal,
            })
        },

        retry: false,

        // 立即尝试，由 fetch 报告网络失败，
        // 不挂起等待联网后补发。
        networkMode: 'always',
    })

    /** 确认后恢复内容；有效的成功请求返回新摘要，其余返回 undefined。 */
    async function restoreVersion(
        // 必须来自当前文档查询结果，
        // 不能使用历史快照的 version。
        expectedVersion: number,
    ): Promise<KnowledgeDocumentResponse | undefined> {
        if (requestRef.current) return undefined

        if (!session || !canManage) {
            setRestoreMessage('当前身份不能恢复历史版本。')
            return undefined
        }

        if (!documentId || documentId.length > 36) {
            setRestoreMessage('文档 ID 无效，请返回文档列表。')
            return undefined
        }

        if (
            typeof version !== 'number' ||
            !Number.isSafeInteger(version) ||
            version < 1 ||
            !Number.isSafeInteger(expectedVersion) ||
            expectedVersion < 1 ||
            version >= expectedVersion
        ) {
            setRestoreMessage('请核对版本：历史版本必须小于当前版本。')
            return undefined
        }

        // actorId 用于刷新本次操作者的缓存，不提交给后端。
        const actorId = session.user.id
        const controller = new AbortController()

        requestRef.current = controller
        setIsRestoring(true)
        setRestoreMessage('')

        try {
            const result = await mutation.mutateAsync({
                accessToken: session.accessToken,
                documentId,
                version,
                expectedVersion,
                signal: controller.signal,
            })

            // 切换页面或身份后，
            // 旧结果也不能交给页面触发跳转。
            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return undefined

            setRestoreMessage(
                '已恢复 v' + version + ' 的内容，当前版本为 v' +
                result.version + '。',
            )

            return result
        } catch (error) {
            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return undefined

            setRestoreMessage(getRestoreMessage(error))
            return undefined
        } finally {
            // 成功、冲突或结果不确定时，
            // 都让相关缓存重新核对。
            // 使用点击时的账号，不使用文档创建者或后来登录的账号。
            void queryClient.invalidateQueries({
                queryKey: ['knowledge-documents', actorId],
            }).catch(() => undefined)

            // 只释放自己的锁，不干扰切换后开始的新请求。
            if (requestRef.current === controller) {
                requestRef.current = null

                if (!controller.signal.aborted) {
                    setIsRestoring(false)
                }
            }
        }
    }

    return { restoreVersion, isRestoring, restoreMessage }
}
