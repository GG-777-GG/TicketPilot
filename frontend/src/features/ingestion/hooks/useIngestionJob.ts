/**
 * 文件职责：按任务 ID 管理单条入库任务的查询、缓存和状态刷新。
 * 作用与配合：读取登录身份，调用 getIngestionJob，向详情界面提供查询结果。
 * 使用位置：供后续任务详情页面或组件调用。
 * 边界：不绘制页面、不修改任务；只查询主管或管理员有权查看的任务。
 */

import { useQuery } from '@tanstack/react-query'

import { useAuth } from '../../auth/hooks/useAuth'
import { getIngestionJob } from '../api'

/**
 * 查询一条任务。
 *
 * 未结束时每 5 秒刷新；
 * 查询出错或任务结束后停止轮询。
 */
export function useIngestionJob(
  // 准备查看的任务 ID。
  // 页面尚未取得 ID 时，可以是 undefined。
  jobId: string | undefined,
) {
  const { session, status: authStatus } = useAuth()

  // 后端只允许主管或管理员查询任务详情。
  const canManage =
    authStatus === 'authenticated' &&
    (session?.user.role === 'admin' ||
      session?.user.role === 'supervisor')

  // typeof 是 JavaScript 自带的类型检查运算符。
  // 这里只检查字符串和长度，不验证任务是否真实存在。
  const hasValidJobId =
    typeof jobId === 'string' &&
    jobId.length >= 1 &&
    jobId.length <= 36

  const query = useQuery({
    // 按账号、角色和任务 ID 区分缓存，不把 JWT 放进键里。
    // detail 用来区分单条查询与列表分页。
    queryKey: [
      'ingestion-jobs',
      session?.user.id ?? null,
      session?.user.role ?? null,
      'detail',
      jobId ?? null,
    ],

    // 有管理权限且取得有效长度的 ID 后，才自动查询。
    enabled: canManage && hasValidJobId,

    // signal 由 Query 提供，用于停止等待本次查询。
    queryFn: ({ signal }) => {
      // 手动 refetch 也可能执行这里，所以再次检查。
      if (!session || !canManage || !hasValidJobId || !jobId) {
        throw new Error('当前身份或任务 ID 不允许查询任务详情')
      }

      return getIngestionJob(
        session.accessToken,
        jobId,
        signal,
      )
    },

    // 请求失败后不自动重试，由页面提供手动刷新入口。
    retry: false,

    // currentQuery 是 Query 管理的本次查询对象。
    refetchInterval: (currentQuery) => {
      // 这里的 error 表示“查询请求失败”，
      // 与后台任务的 failed 状态不同。
      if (currentQuery.state.status === 'error') return false

      // ?. 表示数据尚未返回时，先得到 undefined，
      // 避免直接读取不存在的数据而报错。
      const taskStatus = currentQuery.state.data?.status

      // 任务尚未结束时，每 5000 毫秒重新查询一次。
      if (
        taskStatus === 'queued' ||
        taskStatus === 'running' ||
        taskStatus === 'cancelling'
      ) {
        return 5_000
      }

      // succeeded、failed、cancelled 都是终态。
      return false
    },

    // 页面不可见时，不继续定时查询。
    refetchIntervalInBackground: false,

    // 切回页面时补查一次。
    // 查询已出错时，等待用户手动刷新。
    refetchOnWindowFocus: (currentQuery) =>
      currentQuery.state.status !== 'error',

    // 网络恢复时，不额外自动查询。
    refetchOnReconnect: false,
  })

  return { canManage, hasValidJobId, query }
}