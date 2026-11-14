/**
 * 文件职责：组合上传表单，保存任务筛选和分页状态。
 * 作用与配合：接收上传成功通知，将筛选和分页参数交给任务列表。
 * 使用位置：router.tsx 中 /knowledge/ingestion 对应的工作台页面。
 * 边界：管理页面交互状态，不直接请求后端或管理查询缓存。
 */
// 页面组合任务列表组件。
import { IngestionJobsPanel } from '../components/IngestionJobsPanel'

// 管理取消请求及其操作反馈。
import { useCancelIngestionJob } from '../hooks/useCancelIngestionJob'

// 保存页面当前选择的任务状态。
import { useState } from 'react'

// React 提供的下拉框变化事件类型。
import type { ChangeEvent } from 'react'

// 读取真实角色，决定是否显示筛选控件。
import { useAuth } from '../../auth/hooks/useAuth'

import type { IngestionJobStatus } from '../types'
import styles from './IngestionPage.module.css'

// 文件上传表单，负责收集输入并提交任务。
import { FileUploadForm } from '../components/FileUploadForm'

// 管理失败任务重试及其操作反馈。
import { useRetryIngestionJob } from '../hooks/useRetryIngestionJob'

/** 入库任务页面，后面在这里组合上传表单和任务列表。 */
export function IngestionPage() {
      // all 只表示前端的“全部状态”，不会直接发送给后端。
  const [statusFilter, setStatusFilter] =
    useState<IngestionJobStatus | 'all'>('all')
      // 当前跳过多少条任务；0 表示第一页。
  const [offset, setOffset] = useState(0)

  const { session } = useAuth()

    // 页面保存取消操作状态，再把方法交给任务面板。
    const { cancelJob, cancellingJobId, cancelMessage } =
  useCancelIngestionJob()


    // 页面保存重试状态，将操作和状态传给列表。
    const { retryJob, retryingJobId, retryMessage } =
  useRetryIngestionJob()
  // 这里只决定筛选框是否显示。
  // 实际查询权限仍由 Hook 和后端检查。
  const canManage =
    session?.user.role === 'admin' ||
    session?.user.role === 'supervisor'

  /**
   * 处理任务状态选择变化。
   * event：这次下拉框变化事件。
   * currentTarget：绑定这个事件的 select 元素。
   */
  function handleStatusChange(
    event: ChangeEvent<HTMLSelectElement>,
  ): void {
    // 浏览器读取到的是字符串，需要确认属于允许的选项。
    const value = event.currentTarget.value

    if (
      value === 'all' ||
      value === 'queued' ||
      value === 'running' ||
      value === 'cancelling' ||
      value === 'succeeded' ||
         value === 'failed' ||
      value === 'cancelled'

    ) {
      // 更新状态后，React 会使用新选择重新渲染页面。
      setStatusFilter(value)
              // 切换筛选条件时，同时回到第一页。
      setOffset(0)
    }
  }

    /**
   * 上传任务被后端接收后，显示最新任务。
   * 列表刷新由上传 Hook 负责，这里只调整页面状态。
   */
  function handleSubmitted(): void {
    setStatusFilter('all')
    setOffset(0)
  }

  return (
    <section>
      <h1>知识入库</h1>
      <p>上传企业文档，查看后台处理进度。</p>
      {/* 管理员和主管可以提交文件任务。 */}
      {canManage && (
        <FileUploadForm
          key={session?.user.id}
          onSubmitted={handleSubmitted}
        />
      )}
      {/* 有管理权限时，才显示状态筛选。 */}
      {canManage && (
        <div className={styles.filters}>
          <div className={styles.filterField}>
            <label htmlFor="ingestion-status">
              任务状态
            </label>

            <select
              id="ingestion-status"
              className={styles.select}
              value={statusFilter}
              onChange={handleStatusChange}
            >
              <option value="all">全部状态</option>
              <option value="queued">等待处理</option>
              <option value="running">正在处理</option>
              <option value="cancelling">正在取消</option>
              <option value="succeeded">入库成功</option>
              <option value="failed">处理失败</option>
                <option value="cancelled">已取消</option>
            </select>
          </div>
        </div>
      )}
           {/* 全部状态转换成 undefined，让 API 不发送 status 参数。 */}
{/* 操作反馈放在列表外，任务离开当前筛选后仍然保留。 */}
<div role="status" aria-live="polite" aria-atomic="true">
  {cancelMessage && (
    <p className={styles.feedback}>{cancelMessage}</p>
  )}

  {retryMessage && (
    <p className={styles.feedback}>{retryMessage}</p>
  )}
</div>

<IngestionJobsPanel
  status={statusFilter === 'all' ? undefined : statusFilter}
  offset={offset}
  onOffsetChange={setOffset}
  onCancel={cancelJob}
  cancellingJobId={cancellingJobId}
  onRetry={retryJob}
  retryingJobId={retryingJobId}
/>
    </section>
  )
}
