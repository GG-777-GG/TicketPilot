/**
 * 文件职责：管理当前文档的归档请求、提交状态和结果提示。
 * 作用与配合：调用归档 API，通知 Query 刷新当前操作者的文档列表和详情。
 * 使用位置：供知识文档详情页面调用。
 * 边界：不绘制按钮或确认框；离开页面只停止等待，不撤销已接收的归档。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { archiveKnowledgeDocument } from '../api'


/** 本次归档请求使用的参数快照。 */
type ArchiveDocumentVariables = {
  // 点击时准备归档的文档 ID。
  documentId: string

  // 点击按钮的账号 ID，用于确认身份并刷新该账号的缓存。
  actorId: string

  // 本次请求的停止等待信号。
  signal: AbortSignal
}

/** 提供归档方法、提交状态和结果提示；调用 archiveDocument 才发送请求。 */
export function useArchiveKnowledgeDocument(
  // 当前详情页的文档 ID；尚未取得路由参数时允许为空。
  documentId: string | undefined,
) {
  const { session, status: authStatus } = useAuth()
  const queryClient = useQueryClient()

  // 控制按钮等待状态，以及页面中的操作结果提示。
  const [isArchiving, setIsArchiving] = useState(false)
  const [archiveMessage, setArchiveMessage] = useState('')

  // ref 立即记录正在进行的请求，阻止同一轮渲染内的快速重复点击。
  const requestRef = useRef<AbortController | null>(null)

  const canManage =
    authStatus === 'authenticated' &&
    (session?.user.role === 'admin' ||
      session?.user.role === 'supervisor')

  // 状态由工作台的 key 重置；参数变化或卸载时取消旧请求。
  useEffect(() => {
    return () => {
      const controller = requestRef.current
      requestRef.current = null
      controller?.abort()
    }
  }, [documentId, session?.user.id, session?.accessToken, session?.user.role])

  const mutation = useMutation({
    // 这些参数来自下面 mutateAsync 传入的对象。
    mutationFn: ({
      documentId,
      actorId,
      signal,
    }: ArchiveDocumentVariables) => {
      // 即使方法被直接调用，也再次检查当前身份。
      if (!session || !canManage || session.user.id !== actorId) {
        throw new Error('当前身份不能归档知识文档')
      }

      // signal.throwIfAborted() 在已停止等待时抛出异常，避免开始请求。
      signal.throwIfAborted()
      return archiveKnowledgeDocument(session.accessToken, documentId, signal)
    },

    // 不自动重发归档请求。
    retry: false,

    // 离线时也立即尝试并反馈，不把写操作排队到联网后自动补发。
    networkMode: 'always',
  })

  /** 提交当前文档的归档申请，展示结果并通知相关缓存重新查询。 */
  async function archiveDocument(
    // 当前文档标题，只用于结果提示，不会发送到后端。
    title: string,
  ): Promise<void> {
    if (requestRef.current) return

    if (!session || !canManage) {
      setArchiveMessage('当前身份不能归档知识文档。')
      return
    }

    // 与后端一样检查 ID 长度；文档是否存在由后端判断。
    if (!documentId || documentId.length > 36) {
      setArchiveMessage('文档 ID 缺失或长度不正确，请返回文档列表。')
      return
    }

    // 管理员可能操作别人创建的文档，缓存使用操作者 ID。
    const actorId = session.user.id
    const controller = new AbortController()
    requestRef.current = controller
    setIsArchiving(true)
    setArchiveMessage('')

    try {
      // mutateAsync 执行 mutationFn，并返回可以 await 的 Promise。
      await mutation.mutateAsync({
        documentId,
        actorId,
        signal: controller.signal,
      })

      // 已切换文档、身份或离开页面，不把旧结果写入新界面。
      if (controller.signal.aborted || requestRef.current !== controller) return
      setArchiveMessage(`“${title}”已归档。`)
    } catch (error) {
      if (controller.signal.aborted || requestRef.current !== controller) return

      if (error instanceof ApiError) {
        setArchiveMessage(
          error.status === 401
            ? '登录已失效，请退出后重新登录。'
            : error.status === 403
              ? '当前账号没有归档文档的权限。'
              : error.status === 404
                ? '知识文档不存在，请返回文档列表。'
                : error.message,
        )
      } else {
        // 超时或断网时，后端可能已经执行成功，先查询再决定是否操作。
        setArchiveMessage('暂时无法确认归档结果，请先刷新文档状态，再决定是否重新操作。')
      }
    } finally {
      // 列表和详情的缓存键都以这两个值开头，因此能一起标记为过期。
      // 活跃的查询会重新请求，未显示的分页在下次使用时查询。
      // 即使停止等待，后端仍可能已归档，所以也标记对应账号的缓存。
      void queryClient
        .invalidateQueries({ queryKey: ['knowledge-documents', actorId] })
        .catch(() => undefined)

      // 不等待查询完成，避免把刷新失败当成归档失败。
      // ref 仍属于本次操作时，才释放锁，避免影响后来开始的新请求。
      if (requestRef.current === controller) {
        requestRef.current = null
        if (!controller.signal.aborted) setIsArchiving(false)
      }
    }
  }

  return { archiveDocument, isArchiving, archiveMessage }
}
