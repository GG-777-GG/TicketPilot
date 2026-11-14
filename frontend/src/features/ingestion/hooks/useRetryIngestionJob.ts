/**
 * 文件职责：管理失败任务的手动重试、提交状态和结果提示。
 * 作用与配合：调用重试 API，刷新当前操作者的任务列表。
 * 使用位置：供 IngestionPage 调用，再将重试方法交给任务面板。
 * 边界：不上传新文件、不执行入库；停止等待不会撤销已接收的重试申请。
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { retryIngestionJob } from '../api'

/** 每次执行重试请求需要的参数。 */
type RetryJobVariables = {
  // 准备重试的任务 ID。
  jobId: string

  // 本次点击按钮的账号 ID。
  actorId: string

  // 浏览器停止等待本次请求的信号。
  signal: AbortSignal
}

/**
 * 提供重试方法、正在提交的任务 ID 和结果提示。
 * 只有调用返回的 retryJob，才会真正发送重试请求。
 */
export function useRetryIngestionJob() {
  const { session, status: authStatus } = useAuth()
  const queryClient = useQueryClient()

  // null 表示当前没有正在提交的重试申请。
  const [retryingJobId, setRetryingJobId] =
    useState<string | null>(null)

  // 保存页面需要显示的操作结果。
  const [retryMessage, setRetryMessage] = useState('')

  // ref 立即记录请求，防止快速重复点击。
  const requestRef = useRef<AbortController | null>(null)

  // 前端先检查资格，实际操作权限由后端再次校验。
  const canManage =
    authStatus === 'authenticated' &&
    (session?.user.role === 'admin' ||
      session?.user.role === 'supervisor')

  // 状态由工作台的 key 重置；这里只取消旧请求。
  useEffect(() => {
    return () => {
      const controller = requestRef.current
      requestRef.current = null
      controller?.abort()
    }
  }, [session?.user.id, session?.accessToken, session?.user.role])

  const mutation = useMutation({
    // 参数由下面的 mutateAsync 传入。
    mutationFn: ({
      jobId,
      actorId,
      signal,
    }: RetryJobVariables) => {
      if (
        !session ||
        !canManage ||
        session.user.id !== actorId
      ) {
        throw new Error('当前身份不能重试入库任务')
      }

      // 已停止等待时，不再开始请求。
      signal.throwIfAborted()

      return retryIngestionJob(
        session.accessToken,
        jobId,
        signal,
      )
    },

    // 禁止自动重发 HTTP 请求。
    // 用户仍然可以主动点击“重试”。
    retry: false,

    // 离线时也立即尝试，不等待联网后自动补发。
    networkMode: 'always',
  })

  /**
   * 手动重试一条失败任务。
   *
   * 流程：防重复 → 申请重新排队 → 提示结果 → 刷新列表。
   */
  async function retryJob(
    // 需要重试的任务 ID。
    jobId: string,

    // 用于结果提示的任务标题。
    title: string,
  ): Promise<void> {
    // 已经有申请正在提交时，不重复发送。
    if (requestRef.current) return

    if (!session || !canManage) {
      setRetryMessage('当前身份不能重试入库任务。')
      return
    }

    // 管理员可能操作别人的任务。
    // 刷新列表时要使用操作者 ID，而不是任务提交人 ID。
    const actorId = session.user.id

    const controller = new AbortController()
    requestRef.current = controller

    setRetryingJobId(jobId)
    setRetryMessage('')

    try {
      await mutation.mutateAsync({
        jobId,
        actorId,
        signal: controller.signal,
      })

      // 身份已变化或页面已离开，不更新旧页面。
      if (controller.signal.aborted) return

      // 接口成功只代表已排队，不能提示入库成功。
      setRetryMessage(
        `“${title}”已重新排队，请查看列表中的最新状态。`,
      )
    } catch (error) {
      if (controller.signal.aborted) return

      if (error instanceof ApiError) {
        setRetryMessage(
          error.status === 409
            ? '任务状态已经变化，请查看刷新后的列表。'
            : error.status === 401
              ? '登录已失效，请退出后重新登录。'
              : error.message,
        )
      } else {
        // 超时或断网时，后端可能已经收到申请。
        setRetryMessage(
          '暂时无法确认重试结果，请先查看任务最新状态，再决定是否重新操作。',
        )
      }
    } finally {
      if (!controller.signal.aborted) {
        // 刷新操作者的列表，包括他的分页和筛选结果。
        // 不等待刷新完成，避免把列表查询失败当作重试失败。
        void queryClient
          .invalidateQueries({
            queryKey: ['ingestion-jobs', actorId],
          })
          .catch(() => undefined)

        setRetryingJobId(null)
      }

      // 避免旧请求清理掉后来创建的新请求。
      if (requestRef.current === controller) {
        requestRef.current = null
      }
    }
  }

  return { retryJob, retryingJobId, retryMessage }
}
