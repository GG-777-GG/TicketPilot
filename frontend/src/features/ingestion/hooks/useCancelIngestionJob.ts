/**
 * 文件职责：管理任务取消请求、提交状态和操作反馈。
 * 作用与配合：调用取消 API，通知 Query 刷新当前操作者的任务列表。
 * 使用位置：由 IngestionPage 调用，再将操作传给任务面板。
 * 边界：不执行后台取消；离开页面只停止等待，不撤销已接收的申请。
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { cancelIngestionJob } from '../api'

/** 每次发送取消请求时需要的参数。 */
type CancelJobVariables = {
  // 准备取消的任务 ID。
  jobId: string

  // 点击按钮的账号 ID，用于确认当前身份。
  actorId: string

  // 本次请求的停止等待信号。
  signal: AbortSignal
}

/**
 * 向页面提供取消方法、正在提交的任务 ID 和反馈文字。
 */
export function useCancelIngestionJob() {
  const { session, status: authStatus } = useAuth()
  const queryClient = useQueryClient()

  // null 表示当前没有正在提交的取消申请。
  const [cancellingJobId, setCancellingJobId] =
    useState<string | null>(null)

  // 保存页面需要展示的操作结果。
  const [cancelMessage, setCancelMessage] = useState('')

  // 立即记录正在进行的请求，阻止快速重复点击。
  const requestRef = useRef<AbortController | null>(null)

  // 状态由工作台的 key 重置；参数变化或卸载时取消旧请求。
  useEffect(() => {
    return () => {
      const controller = requestRef.current
      requestRef.current = null
      controller?.abort()
    }
  }, [session?.user.id, session?.accessToken, session?.user.role])

  const mutation = useMutation({
    // 参数来自下面 mutateAsync 传入的对象。
    mutationFn: ({
      jobId,
      actorId,
      signal,
    }: CancelJobVariables) => {
      // 前端检查操作资格，后端仍会独立验证权限。
      if (
        !session ||
        authStatus !== 'authenticated' ||
        session.user.id !== actorId ||
        !['admin', 'supervisor'].includes(session.user.role)
      ) {
        throw new Error('当前身份不能取消入库任务')
      }

      // 已停止等待时，不再开始请求。
      signal.throwIfAborted()

      return cancelIngestionJob(
        session.accessToken,
        jobId,
        signal,
      )
    },

    // 不自动重发，也不离线排队等待联网后补发。
    retry: false,
    networkMode: 'always',
  })

  /**
   * 用户点击取消时执行。
   *
   * 流程：发送申请 → 展示结果 → 刷新列表。
   */
  async function cancelJob(
    // 目标任务的唯一 ID。
    jobId: string,

    // 用于反馈中指出正在操作哪条任务。
    title: string,
  ): Promise<void> {
    // 已有请求正在提交时，不重复发送。
    if (requestRef.current) return

    if (
      !session ||
      authStatus !== 'authenticated' ||
      !['admin', 'supervisor'].includes(session.user.role)
    ) {
      setCancelMessage('当前身份不能取消入库任务。')
      return
    }

    // 保存点击时的操作者。
    // 管理员可能取消别人的任务，所以不能使用任务提交人代替。
    const actorId = session.user.id

    const controller = new AbortController()
    requestRef.current = controller

    setCancellingJobId(jobId)
    setCancelMessage('')

    try {
      const job = await mutation.mutateAsync({
        jobId,
        actorId,
        signal: controller.signal,
      })

      // 页面已经离开或身份发生变化，不更新旧页面。
      if (controller.signal.aborted) return

      setCancelMessage(
        job.status === 'cancelled'
          ? `“${title}”已取消。`
          : `已提交“${title}”的取消申请，正在等待后台安全停止。`,
      )
    } catch (error) {
      if (controller.signal.aborted) return

      if (error instanceof ApiError) {
        setCancelMessage(
          error.status === 409
            ? '任务状态已经变化，请查看刷新后的列表。'
            : error.status === 401
              ? '登录已失效，请退出后重新登录。'
              : error.message,
        )
      } else {
        // 超时不代表后端没有收到申请。
        setCancelMessage(
          '暂时无法确认取消结果，请查看列表最新状态后再决定是否重试。',
        )
      }
    } finally {
      // finally 在成功或失败后都会执行，用于收尾。
      if (!controller.signal.aborted) {
        // 让当前操作者的任务列表重新查询。
        // 不等待刷新完成，避免混淆取消结果和列表查询结果。
        void queryClient
          .invalidateQueries({
            queryKey: ['ingestion-jobs', actorId],
          })
          .catch(() => undefined)

        setCancellingJobId(null)
      }

      // 只清理属于本次操作的请求记录。
      if (requestRef.current === controller) {
        requestRef.current = null
      }
    }
  }

  return { cancelJob, cancellingJobId, cancelMessage }
}
