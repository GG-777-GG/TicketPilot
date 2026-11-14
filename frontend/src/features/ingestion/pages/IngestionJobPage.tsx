/**
 * 文件职责：展示一条入库任务的状态、来源、时间和处理结果。
 * 作用与配合：从页面地址取得任务 ID，通过 useIngestionJob 查询和刷新。
 * 使用位置：工作台的 /knowledge/ingestion/:jobId 路由。
 * 边界：只读展示任务，不执行入库、取消或重试，不展示内部租约和存储路径。
 */
import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { useIngestionJob } from '../hooks/useIngestionJob'
import type { IngestionJobStatus, IngestionProcessingStage } from '../types'
import styles from './IngestionJobPage.module.css'

// Record 要求每一种任务状态都有对应的中文名称。
const STATUS_LABELS: Record<IngestionJobStatus, string> = {
  queued: '等待处理',
  running: '正在处理',
  cancelling: '正在取消',
  succeeded: '入库成功',
  failed: '处理失败',
  cancelled: '已取消',
}

const STAGE_LABELS: Record<IngestionProcessingStage, string> = {
  parsing: '解析文件（必要时 OCR）',
  cleaning: '清洗文字',
  chunking: '切分知识片段',
  embedding: '生成向量',
  indexing: '保存文档与向量',
}

/** 将后端返回的时间字符串转换成北京时间。 */
function formatTime(
  // 后端返回的 ISO 时间字符串，例如带有 Z 的 UTC 时间。
  value: string,
): string {
  // Date 是 JavaScript 内置的日期对象。
  // toLocaleString 按指定语言和时区生成可读文字。
  return new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  })
}

/** 组合任务详情、异常提示、返回链接和手动刷新操作。 */
export function IngestionJobPage() {
  // useParams 读取路由中 :jobId 对应的值。
  const { jobId } = useParams()
  const { canManage, hasValidJobId, query } = useIngestionJob(jobId)
  const { signOut } = useAuth()

  // 保存页面标题元素，进入详情时将键盘焦点移到标题。
  const titleRef = useRef<HTMLHeadingElement | null>(null)

  useEffect(() => {
    const previousTitle = document.title
    document.title = '任务详情 · TicketPilot'
    titleRef.current?.focus()

    // 离开页面时恢复原来的浏览器标签标题。
    return () => {
      document.title = previousTitle
    }
  }, [jobId])

  const errorStatus =
    query.error instanceof ApiError ? query.error.status : undefined

  // 身份或访问权限错误时，提供返回或重新登录，不继续重复查询。
  const showRefresh =
    canManage &&
    hasValidJobId &&
    ![401, 403, 404].includes(errorStatus ?? 0)

  /** 手动刷新当前任务；已有查询进行时不重复启动。 */
  function refreshJob(): void {
    if (query.isFetching) return
    void query.refetch({ cancelRefetch: false })
  }

  /** 根据权限、参数和查询结果，选择详情区域需要展示的内容。 */
  function renderDetails() {
    // 禁用的查询也可能处于 pending，先检查权限和任务 ID。
    if (!canManage) {
      return <p role="alert">当前账号没有任务查看权限，请使用主管或管理员账号。</p>
    }

    if (!hasValidJobId) {
      return <p role="alert">任务 ID 缺失或长度不正确，请返回任务列表。</p>
    }

    if (query.isPending) {
      return (
        <p role="status">
          {query.fetchStatus === 'paused'
            ? '等待网络连接后加载任务详情。'
            : '正在加载任务详情……'}
        </p>
      )
    }

    // 查询失败先展示错误，避免把旧缓存显示成最新状态。
    if (query.isError) {
      return (
        <div className={styles.error}>
          <p role="alert">
            {errorStatus === 401
              ? '登录已失效，请重新登录。'
              : errorStatus === 403
                ? '当前账号没有查看这条任务的权限。'
                : errorStatus === 404
                  ? '任务不存在或你无权查看。'
                  : '暂时无法获取任务详情，请稍后点击刷新状态。'}
          </p>

          {errorStatus === 401 && (
            <button type="button" className={styles.button} onClick={signOut}>
              重新登录
            </button>
          )}
        </div>
      )
    }

    const job = query.data

    // 每组数据分别是“字段名称”和“展示值”。
    const fields = [
      ['任务 ID', job.id],
      ['原始文件或来源', job.source_uri ?? '未提供'],
      ['可见范围', job.visibility === 'restricted' ? '受限可见' : '内部可见'],
      ['处理次数', job.attempt_count],
      ['处理阶段', job.processing_stage ? STAGE_LABELS[job.processing_stage] : '暂无阶段记录'],
      ['生成的文档 ID', job.document_id ?? '尚未生成文档'],
      ['提交时间（北京时间）', formatTime(job.created_at)],
      ['更新时间（北京时间）', formatTime(job.updated_at)],
    ]

    return (
      <>
        <div className={styles.summary}>
          <h2>{job.title}</h2>
          <p role="status">
            <span className={styles.badge} data-status={job.status}>
              {STATUS_LABELS[job.status]}
            </span>
          </p>
        </div>

        {/* dl 表示名称和值的列表，dt 是名称，dd 是对应的值。 */}
        <dl className={styles.fields}>
          {fields.map(([label, value]) => (
            <div key={label} className={styles.field}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>

        {/* 重新排队后也可能保留旧错误，因此明确标为上次失败原因。 */}
        {(job.status === 'failed' || job.last_error) && (
          <aside className={styles.failure}>
            <h3>上次失败原因</h3>
            <p>{job.last_error ?? '未记录具体原因。'}</p>
          </aside>
        )}
      </>
    )
  }

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div>
          {/* tabIndex=-1 允许程序聚焦标题，不加入正常 Tab 顺序。 */}
          <h1 ref={titleRef} tabIndex={-1}>任务详情</h1>
          <p className={styles.description}>查看入库任务的处理状态与结果。</p>
        </div>

        <div className={styles.actions}>
          {/* Link 在应用内切换页面，不重新加载整个网页。 */}
          <Link to="/knowledge/ingestion" className={styles.button}>
            返回任务列表
          </Link>

          {showRefresh && (
            <button
              type="button"
              className={styles.button}
              onClick={refreshJob}
              aria-disabled={query.isFetching}
            >
              {query.isFetching ? '刷新中……' : '刷新状态'}
            </button>
          )}
        </div>
      </header>

      <div className={styles.card} aria-busy={query.isFetching}>
        {renderDetails()}
      </div>
    </section>
  )
}
