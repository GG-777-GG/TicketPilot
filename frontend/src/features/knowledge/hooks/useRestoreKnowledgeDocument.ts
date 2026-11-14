/**
 * 文件职责：管理当前文档的恢复请求、提交状态和结果提示。
 * 作用与配合：调用恢复 API，通知 Query 刷新当前操作者的文档列表和详情。
 * 使用位置：供知识文档详情页面调用。
 * 边界：不绘制按钮或确认框；停止等待不撤销后端已经执行的恢复。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { restoreKnowledgeDocument } from '../api'

/** 本次恢复请求使用的参数快照。 */
type RestoreDocumentVariables = {
  // 点击时准备恢复的文档 ID。
  documentId: string

  // 点击按钮的账号 ID，用于确认身份并刷新该账号的缓存。
  actorId: string

  // 本次请求的停止等待信号。
  signal: AbortSignal
}

/** 提供恢复方法、提交状态和结果提示；调用 restoreDocument 才发送请求。 */
export function useRestoreKnowledgeDocument(
  // 当前详情页的文档 ID；尚未取得路由参数时允许为空。
  documentId: string | undefined,
) {
  const { session, status: authStatus } = useAuth()
  const queryClient = useQueryClient()

  // 控制恢复按钮的等待状态，以及页面中的结果提示。
  const [isRestoring, setIsRestoring] = useState(false)
  const [restoreMessage, setRestoreMessage] = useState('')

  // 立即记录请求，阻止同一轮渲染内快速重复点击。
  const requestRef = useRef<AbortController | null>(null)

  const canManage =
    authStatus === 'authenticated' &&
    (session?.user.role === 'admin' ||
      session?.user.role === 'supervisor')

  // 状态由工作台的 key 重置；这里只停止等待旧请求。
  useEffect(() => {
    return () => {
      const controller = requestRef.current
      requestRef.current = null
      controller?.abort()
    }
  }, [documentId, session?.user.id, session?.accessToken, session?.user.role])

  const mutation = useMutation({
    // 参数由下面的 mutateAsync 传入。
    mutationFn: ({
      documentId,
      actorId,
      signal,
    }: RestoreDocumentVariables) => {
      // 方法被直接调用时，也检查当前身份与点击时的账号是否一致。
      if (!session || !canManage || session.user.id !== actorId) {
        throw new Error('当前身份不能恢复知识文档')
      }

      // 已停止等待时抛出异常，避免再开始请求。
      signal.throwIfAborted()
      return restoreKnowledgeDocument(session.accessToken, documentId, signal)
    },

    // 不自动重发，也不离线排队等待联网后补发。
    retry: false,
    networkMode: 'always',
  })

  /** 提交当前文档的恢复申请，显示结果并通知相关缓存重新查询。 */
  async function restoreDocument(
    // 当前文档标题，只用于结果提示，不会发送到后端。
    title: string,
  ): Promise<void> {
    if (requestRef.current) return

    if (!session || !canManage) {
      setRestoreMessage('当前身份不能恢复知识文档。')
      return
    }

    // 检查 ID 长度；文档是否存在、状态能否恢复由后端检查。
    if (!documentId || documentId.length > 36) {
      setRestoreMessage('文档 ID 缺失或长度不正确，请返回文档列表。')
      return
    }

    // 使用操作者 ID，不能使用文档创建者 ID 代替。
    const actorId = session.user.id
    const controller = new AbortController()
    requestRef.current = controller
    setIsRestoring(true)
    setRestoreMessage('')

    try {
      // mutateAsync 执行 mutationFn，并返回可以 await 的 Promise。
      await mutation.mutateAsync({
        documentId,
        actorId,
        signal: controller.signal,
      })

      // 已切换文档或身份时，不把旧请求结果写入新界面。
      if (controller.signal.aborted || requestRef.current !== controller) return
      setRestoreMessage(`“${title}”已恢复，可重新参与知识检索。`)
    } catch (error) {
      if (controller.signal.aborted || requestRef.current !== controller) return

      if (error instanceof ApiError) {
        setRestoreMessage(
          error.status === 409
            ? '文档当前状态不允许恢复，请查看刷新后的状态。'
            : error.status === 401
              ? '登录已失效，请退出后重新登录。'
              : error.status === 403
                ? '当前账号没有恢复文档的权限。'
                : error.status === 404
                  ? '知识文档不存在，请返回文档列表。'
                  : error.message,
        )
      } else {
        // 超时或断网时，后端可能已执行成功，先查询再决定是否操作。
        setRestoreMessage('暂时无法确认恢复结果，请先刷新文档状态，再决定是否重新操作。')
      }
    } finally {
      // 同时标记当前操作者的列表和详情缓存为过期。
      // 活跃查询会重新请求；尚未显示的分页在下次使用时查询。
      // 停止等待时后端仍可能已执行，因此也标记对应账号的缓存。
      void queryClient
        .invalidateQueries({ queryKey: ['knowledge-documents', actorId] })
        .catch(() => undefined)

      // 不等待查询完成，避免把刷新失败误认为恢复失败。
      // 只释放本次请求的锁，不影响后来开始的新请求。
      if (requestRef.current === controller) {
        requestRef.current = null
        if (!controller.signal.aborted) setIsRestoring(false)
      }
    }
  }

  return { restoreDocument, isRestoring, restoreMessage }
}
