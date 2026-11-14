/**
  * 文件职责：分页展示任务、处理阶段及操作，并提供任务详情入口。
 * 作用与配合：接收父页面的筛选和分页参数，通过 Hook 查询，点击分页按钮时通知父页面更新位置。
 * 使用位置：IngestionPage 的任务列表区域，样式来自同名 CSS Module。
 * 边界：不独立保存分页状态，不上传文件，也不执行后台入库任务。
 */

// 用于区分后端返回的 HTTP 错误。
import { ApiError } from '../../../shared/api/http'

// 401 时提供退出当前会话、重新登录的操作。
import { useAuth } from '../../auth/hooks/useAuth'

// 读取任务列表、查询状态和刷新方法。
import { useIngestionJobs } from '../hooks/useIngestionJobs'

// Link 用于应用内页面跳转。
import { Link } from 'react-router'

import type {
  IngestionJobStatus,
  IngestionProcessingStage,
} from '../types'
import styles from './IngestionJobsPanel.module.css'

/** 父页面传给任务面板的参数。 */
type IngestionJobsPanelProps = {
  // 查询哪种状态；省略时查询全部状态。
  status?: IngestionJobStatus

  // 查询时跳过多少条任务。
  offset: number

  // 通知父页面切换分页位置。
  onOffsetChange: (nextOffset: number) => void

  // 通知父页面取消指定任务。
  onCancel: (jobId: string, title: string) => Promise<void>

  // 正在提交取消申请的任务 ID。
  cancellingJobId: string | null

  // 通知父页面重试指定任务。
  // jobId 是目标任务 ID，title 用于展示操作反馈。
  onRetry: (jobId: string, title: string) => Promise<void>

  // 正在提交重试申请的任务 ID；空闲时为 null。
  retryingJobId: string | null
}

// 将后台阶段转换为用户能读懂的文字。
const STAGE_LABELS: Record<IngestionProcessingStage, string> = {
  parsing: '解析文件（必要时 OCR）',
  cleaning: '清洗文字',
  chunking: '切分知识片段',
  embedding: '生成向量',
  indexing: '保存文档与向量',
}

// Record 是 TypeScript 提供的类型工具。
// 表示每一种任务状态，都必须对应一段中文文字。
const STATUS_LABELS: Record<IngestionJobStatus, string> = {
  queued: '等待处理',
  running: '正在处理',
  succeeded: '入库成功',
  failed: '处理失败',
  cancelled: '已取消',
  cancelling: '正在取消',
}

/**
 * 展示最近的入库任务。
 * status：页面选择的任务状态，不传表示查询全部状态。
 * 覆盖无权限、加载中、查询失败、空结果和正常列表。
 */
export function IngestionJobsPanel({
  // 从页面传入的参数对象中取出任务状态。
    status,
    offset,
    onOffsetChange,
  // 父页面提供的取消方法和提交状态。
  onCancel,
  cancellingJobId,
      // 父页面提供的重试方法和提交状态。
  onRetry,
  retryingJobId,
}: IngestionJobsPanelProps) {
    // 筛选条件和分页位置一起交给查询 Hook。
  const { canManage, query } = useIngestionJobs({ status, offset })
  const { signOut } = useAuth()

  /** 手动刷新列表；已有请求进行时，不另外启动重复刷新。 */
  function refreshJobs(): void {
    // refetch 返回 Promise，这里由 Query 管理结果和错误。
    void query.refetch({ cancelRefetch: false })
  }

  // 先判断权限，避免把未启用的查询误显示成一直加载。
  if (!canManage) {
    return (
      <p className={styles.panel}>
        当前账号没有入库管理权限，请使用主管或管理员账号。
      </p>
    )
  }

  // 第一次查询尚未拿到结果。
  if (query.isPending) {
    return (
      <p className={styles.panel} role="status">
        {query.fetchStatus === 'paused'
          ? '等待网络连接后加载任务。'
          : '正在加载入库任务……'}
      </p>
    )
  }

  // 这是“查询接口失败”，与某条任务处理失败不同。
  if (query.isError) {
    const needsLogin =
      query.error instanceof ApiError &&
      query.error.status === 401

    return (
      <div className={styles.panel}>
        <p className={styles.error} role="alert">
          {query.error instanceof ApiError
            ? query.error.message
            : '暂时无法查询任务，请检查网络或后端服务。'}
        </p>

        <button
          type="button"
          className={styles.button}
          onClick={needsLogin ? signOut : refreshJobs}
          disabled={query.isFetching}
        >
          {needsLogin ? '重新登录' : '重新查询'}
        </button>
      </div>
    )
  }

    // page 是实际返回的分页数据。
  // 必须放在加载、错误等提前 return 的判断之后。
  const page = query.data

  const hasPrevious = page.offset > 0
  const hasNext = page.offset + page.limit < page.total

  // 离线时也可能保留占位结果，因此两种状态都要检查。
  const pagingBusy = query.isFetching || query.isPlaceholderData


    /** 通知父页面切换到上一页。 */
    function goPrevious(): void {
      // 正在加载或已经没有上一页时，不执行。
      if (pagingBusy || !hasPrevious) return

      // Math.max 取较大的值，保证偏移量不会小于 0。
      onOffsetChange(Math.max(0, page.offset - page.limit))
    }

  /** 通知父页面切换到下一页。 */
  function goNext(): void {
    if (pagingBusy || !hasNext) return

    onOffsetChange(page.offset + page.limit)
  }

  /** 当前页因数据变化而变空时，返回第一页。 */
  function goFirst(): void {
    if (pagingBusy) return

    onOffsetChange(0)
  }

  // 排除加载和错误状态后，可以读取成功返回的 data。
  return (
    <section
      className={styles.panel}
      aria-labelledby="ingestion-jobs-title"
    >
      <div className={styles.heading}>
        <div>
          <h2
            id="ingestion-jobs-title"
            className={styles.title}
          >
            最近任务
          </h2>

          {/* 总数和当前显示数量分别说明，避免误认为只有这些任务。 */}
                <p className={styles.meta}>
            符合条件共 {page.total} 条，
            {page.items.length === 0 ? (
              <>当前页显示 0 条。</>
            ) : (
              <>
                当前显示第 {page.offset + 1}–
                {page.offset + page.items.length} 条。
              </>
            )}
          </p>
        </div>

        <button
          type="button"
          className={styles.button}
          onClick={refreshJobs}
          disabled={pagingBusy}
        >
          {query.isFetching ? '刷新中……' : '刷新列表'}
        </button>
      </div>

      {/* 占位内容仍是上一页结果，需要明确告知用户。 */}
      {query.isPlaceholderData && (
        <p className={styles.meta} role="status">
          {query.fetchStatus === 'paused'
            ? '等待网络连接，暂时显示上一页结果。'
            : '正在加载所选页，暂时显示上一页结果。'}
        </p>
      )}
      {query.data.items.length === 0 ? (
                <div>
          <p className={styles.meta}>
            {page.offset > 0
              ? '当前页暂无任务，可以返回第一页重新查看。'
              : '当前条件下暂无入库任务。'}
          </p>

          {/* 任务状态变化可能导致当前页变空，提供恢复入口。 */}
          {page.offset > 0 && (
            <button
              type="button"
              className={styles.button}
              aria-disabled={pagingBusy}
              onClick={goFirst}
            >
              返回第一页
            </button>
          )}
        </div>
      ) : (
        <ul className={styles.list} aria-label="入库任务列表">
          {/* map 将每条任务转换为一个列表项。 */}
          {query.data.items.map((job) => (
            <li key={job.id} className={styles.item}>
              <div className={styles.heading}>
                <h3 className={styles.itemTitle}>
                  {job.title}
                </h3>

                {/* data-status 让 CSS 根据真实状态设置颜色。 */}
                <span
                  className={styles.badge}
                  data-status={job.status}
                >
                  {STATUS_LABELS[job.status]}
                </span>
              </div>

              {/* 显示 Worker 实际领取处理的次数。 */}
              <p className={styles.meta}>
                处理次数：{job.attempt_count}
              </p>

               {/* 运行中显示当前阶段。 */}
              {job.status === 'running' && (
                <p className={styles.meta} role="status">
                  当前阶段：
                  {job.processing_stage
                    ? STAGE_LABELS[job.processing_stage]
                    : '等待阶段信息'}
                </p>
              )}

              {/* 失败后保留最后上报的阶段，方便定位。 */}
              {job.status === 'failed' && job.processing_stage && (
                <p className={styles.meta}>
                  失败阶段：{STAGE_LABELS[job.processing_stage]}
                </p>
              )}

              {/* 重新排队后也可能保留旧错误，因此明确叫“上次失败原因”。 */}
              {job.last_error && (
                <p className={styles.error}>
                  上次失败原因：{job.last_error}
                </p>
              )}
                {/* 等待、运行和正在取消的任务显示操作区域。 */}
{(job.status === 'queued' ||
  job.status === 'running' ||
  job.status === 'cancelling') && (
  <div className={styles.cancelAction}>
    <button
      type="button"
      className={`${styles.button} ${styles.cancelButton}`}
      aria-label={`取消任务：${job.title}`}
      disabled={
        query.isPlaceholderData ||
        cancellingJobId !== null ||
        job.status === 'cancelling'
      }
      onClick={() => {
        // 点击时才执行，不能在渲染列表时直接调用。
        // 错误由取消 Hook 处理。
        void onCancel(job.id, job.title)
      }}
    >
      {job.status === 'cancelling'
        ? '取消中……'
        : cancellingJobId === job.id
          ? '正在提交……'
          : '取消任务'}
    </button>
  </div>
)}
                {/* 只有处理失败的任务提供重试入口。 */}
{job.status === 'failed' && (
  <div className={styles.retryAction}>
    <button
      type="button"
      className={styles.button}
      aria-label={`重试任务：${job.title}`}
      disabled={
        query.isPlaceholderData ||
        retryingJobId !== null
      }
      onClick={() => {
        // 点击后才执行；请求错误由重试 Hook 处理。
        void onRetry(job.id, job.title)
      }}
    >
      {retryingJobId === job.id
        ? '正在提交重试……'
        : '重试任务'}
    </button>
  </div>
)}
                {/* 所有状态的任务都可以查看详情。 */}
<div className={styles.detailAction}>
  <Link
    to={`/knowledge/ingestion/${encodeURIComponent(job.id)}`}
    className={styles.detailLink}
    aria-label={`查看任务详情：${job.title}`}
  >
    查看详情
  </Link>
</div>
            </li>
          ))}
        </ul>
      )}
              {/* 父页面保存分页状态，按钮只负责通知它修改。 */}
      <nav
        className={styles.pagination}
        aria-label="入库任务分页"
      >
        <span className={styles.meta}>
          每页最多 {page.limit} 条
        </span>

        <div className={styles.pageActions}>
          <button
            type="button"
            className={styles.button}
            aria-disabled={pagingBusy || !hasPrevious}
            onClick={goPrevious}
          >
            上一页
          </button>

          <button
            type="button"
            className={styles.button}
            aria-disabled={pagingBusy || !hasNext}
            onClick={goNext}
          >
            下一页
          </button>
        </div>
      </nav>
    </section>
  )
}
