/**
 * 文件职责：管理有权账号的入库任务查询，默认获取最近 5 条并提供查询状态和刷新能力。
 * 作用与配合：读取 useAuth、调用列表 API，按用户、角色、分页和状态筛选区分缓存，键中不含 JWT。
 * 使用位置：供 IngestionJobsPanel 展示任务，并通过 query.refetch 手动刷新或重新查询。
 * 边界：仅主管和管理员可查询；可见时轮询，查询出错后暂停轮询；不上传文档或渲染界面。
 */

//queryKey	区分缓存属于哪个用户、哪一页、哪种筛选
// enabled	是否允许自动查询
// queryFn	查询时实际执行哪个函数

// TanStack Query 提供的 Hook，管理查询数据和请求状态。
import { useQuery } from '@tanstack/react-query'

// 读取当前登录身份。
import { useAuth } from '../../auth/hooks/useAuth'

// 已经写好的任务列表请求函数。
import { listIngestionJobs } from '../api'

import type { IngestionJobStatus } from '../types'

/** 页面可以传入的分页和筛选参数。 */
type UseIngestionJobsOptions = {
  // 每页数量，默认 5 条；后端允许 1～100。
  limit?: number

  // 跳过的条数，默认 0，表示从第一页开始。
  offset?: number

  // 可选的任务状态；不传表示查询所有状态。
  status?: IngestionJobStatus
}

/**
 * 管理当前用户的入库任务查询。
 *
 * 根据登录身份决定是否查询；
 * 按身份、分页和筛选条件区分缓存；
 * 页面可见时定时刷新，查询出错时暂停轮询。
 */
export function useIngestionJobs(
  // 默认空对象，允许页面直接调用 useIngestionJobs()。
  options: UseIngestionJobsOptions = {},
) {
  // 将登录状态 status 改用 authStatus 这个局部名称，
  // 避免与任务筛选的 status 混淆。
  const { session, status: authStatus } = useAuth()

  const limit = options.limit ?? 5
  const offset = options.offset ?? 0

  // 后端只允许主管和管理员管理入库任务。
  // 前端控制是否发请求，后端仍会独立验证权限。
  const canManage =
    authStatus === 'authenticated' &&
    (
      session?.user.role === 'admin' ||
      session?.user.role === 'supervisor'
    )

  const query = useQuery({
    // 缓存的标识：不同用户、角色、页码或筛选条件分别保存。
    // JWT 不放进缓存键。
    queryKey: [
      'ingestion-jobs',
      session?.user.id ?? null,
      session?.user.role ?? null,
      {
        limit,
        offset,
        status: options.status ?? null,
      },
    ],

    // 只有身份已确认且角色允许时，才自动查询。
    enabled: canManage,

    // 真正需要查询时，TanStack Query 调用这个函数。
    // signal 由 TanStack Query 提供，用来取消请求等待。
    queryFn: ({ signal }) => {
      // 手动 refetch 也可能执行 queryFn，因此再次检查。
      if (!session || !canManage) {
        throw new Error('当前身份不能查询入库任务')
      }

      return listIngestionJobs({
        // 从当前会话读取 JWT。
        accessToken: session.accessToken,

        // 将页面需要的分页和筛选条件传给 API 函数。
        limit,
        offset,
        status: options.status,

        // 一直传递到 requestJson 和 fetch。
        signal,
      })
    },

          // 翻页等待时，暂时保留同一查询范围的上一页结果。
    // previousData：上次成功的数据。
    // previousQuery：这份数据所属的查询。
    placeholderData: (previousData, previousQuery) => {
      const previousKey = previousQuery?.queryKey

      // queryKey 的第四项保存分页与筛选条件。
      const previousOptions = previousKey?.[3]

      // 用户、角色、筛选条件或每页数量变化时，
      // 不允许借用旧结果，避免混用不同范围的数据。
      // in 用来检查对象中是否存在指定属性。
      if (
        !canManage ||
        previousKey?.[0] !== 'ingestion-jobs' ||
        previousKey?.[1] !== session?.user.id ||
        previousKey?.[2] !== session?.user.role ||
        !previousOptions ||
        typeof previousOptions !== 'object' ||
        !('status' in previousOptions) ||
        previousOptions.status !== (options.status ?? null) ||
        !('limit' in previousOptions) ||
        previousOptions.limit !== limit
      ) {
        return undefined
      }

      return previousData
    },

    // 正常情况下，每 5000 毫秒查询一次。
    // currentQuery 是本次查询对象。
    // 这里的 error 表示查询失败，不是某条任务的 failed 状态。
    refetchInterval: (currentQuery) =>
      currentQuery.state.status === 'error' ? false : 5_000,

    // 页面不可见时，不继续发起定时轮询请求。
    refetchIntervalInBackground: false,

    // 任务页重新可见时，正常状态下补查一次。
    // 这里覆盖全局设置；已有查询错误时不因此刷新。
    refetchOnWindowFocus: (currentQuery) =>
      currentQuery.state.status !== 'error',

    // 关闭网络重新连接时额外触发的刷新。
    refetchOnReconnect: false,
  })

  // 页面拿到权限判断，以及查询的数据、状态和操作。
  return { canManage, query }
}
