/**
 * 文件职责：管理文件入库任务提交的状态、权限和成功后的列表刷新。
 * 作用与配合：调用 submitFileIngestionJob，向上传表单提供进度、结果和错误。
 * 使用位置：供后续文件上传表单调用；创建 Hook 本身不会提交文件。
 * 边界：不绘制表单、不执行解析或 OCR；取消等待需要表单实际触发 signal。
 */

import {
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'

import { useAuth } from '../../auth/hooks/useAuth'
import { submitFileIngestionJob } from '../api'
import type { FileIngestionJobInput } from '../types'

/** 每次真正提交时，需要交给 mutateAsync 的参数。 */
type SubmitFileJobVariables = {
  // 本次提交的文件、标题和可见范围。
  input: FileIngestionJobInput

  // 由上传表单创建；离开页面时，表单负责触发取消。
  signal: AbortSignal
}

/**
 * 提供文件任务的提交操作和状态。
 * 调用这个 Hook 后，还需要由表单主动调用 mutateAsync 才会上传。
 */
export function useSubmitFileIngestionJob() {
  // 读取当前已经确认的登录身份。
  const { session, status: authStatus } = useAuth()

  // 读取管理任务列表缓存的客户端。
  const queryClient = useQueryClient()

  return useMutation({
    // mutationFn 是真正执行提交的函数。
    // 参数对象里包含本次上传数据和取消信号。
    mutationFn: ({
      input,
      signal,
    }: SubmitFileJobVariables) => {
      // 前端先检查身份和角色，后端仍会独立校验。
      if (
        !session ||
        authStatus !== 'authenticated' ||
        !['admin', 'supervisor'].includes(session.user.role)
      ) {
        throw new Error('当前身份不能提交入库任务')
      }

      // 已经取消时，不再开始上传。
      signal.throwIfAborted()

      return submitFileIngestionJob(
        session.accessToken,
        input,
        signal,
      )
    },

    // 上传失败后不自动重发，避免重复创建任务。
    retry: false,

    // 点击时立即尝试；离线时失败，
    // 不排队等待联网后自动补交。
    networkMode: 'always',

    // 提交接口成功返回任务后执行。
    // job：后端返回的任务记录。
    // variables：这一次提交时传入的参数对象。
    onSuccess: (job, variables) => {
      // 已取消等待时，不再触发这里的列表刷新。
      if (variables.signal.aborted) return

      // 使用后端返回的真实提交人，定位相关列表缓存。
      // 当前显示的列表会刷新，其他分页再次使用时重新查询。
      // 不直接向缓存插入任务，也不使用可能已经切换的新账号。
      void queryClient
        .invalidateQueries({
          queryKey: ['ingestion-jobs', job.requested_by],
        })
        // 列表刷新失败由列表自身展示，
        // 不改变“提交接口已经成功”的结果。
        .catch(() => undefined)
    },
  })
}