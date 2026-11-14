/**
 * 文件职责：展示知识文档摘要，保存分页位置并提供手动刷新。
 * 作用与配合：调用 useKnowledgeDocuments，展示文档摘要并提供详情入口。
 * 使用位置：工作台的 /knowledge/documents 路由。
 * 边界：只读展示，不在前端伪造筛选，不执行归档或修改正文。
 */
import {useEffect, useRef, useState} from 'react'
import {Link} from 'react-router'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {useKnowledgeDocuments} from '../hooks/useKnowledgeDocuments'
import type {KnowledgeDocumentStatus} from '../types'
import styles from './KnowledgeDocumentsPage.module.css'

// 每种文档状态都有对应的中文名称。
const STATUS_LABELS: Record<KnowledgeDocumentStatus, string> = {
    pending: '等待处理',
    processing: '正在处理',
    ready: '已就绪',
    failed: '处理失败',
    archived: '已归档',
}

/** 将后端返回的时间字符串显示为北京时间。 */
function formatTime(
    // 后端返回的 ISO 时间字符串。
    value: string,
): string {
    // Date 是 JavaScript 内置日期对象，toLocaleString 生成本地化文字。
    return new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
    })
}

/** 组合文档列表、加载与错误提示，以及分页和刷新操作。 */
export function KnowledgeDocumentsPage() {
    // offset 记录跳过多少篇文档，0 表示第一页。
    const [offset, setOffset] = useState(0)
    const {canManage, query} = useKnowledgeDocuments({offset})
    const {signOut} = useAuth()
    const titleRef = useRef<HTMLHeadingElement | null>(null)

    // 进入页面时聚焦标题；翻页或刷新不会重新移动焦点。
    useEffect(() => {
        const previousTitle = document.title
        document.title = '知识文档 · TicketPilot'
        titleRef.current?.focus()
        return () => {
            document.title = previousTitle
        }
    }, [])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined

    // 过期身份或被拒绝的权限不继续查询，由用户返回或重新登录。
    const showActions = canManage && errorStatus !== 401 && errorStatus !== 403
    const pagingBusy = query.isFetching || query.isPlaceholderData

    /** 手动刷新当前页，已有查询进行时不启动重复请求。 */
    function refreshDocuments(): void {
        if (!showActions || query.isFetching) return
        void query.refetch({cancelRefetch: false})
    }

    /** 切换到上一页，Math.max 保证偏移量不会小于 0。 */
    function goPrevious(): void {
        const page = query.data
        if (pagingBusy || !page || page.offset === 0) return
        setOffset(Math.max(0, page.offset - page.limit))
    }

    /** 切换到下一页，超过文档总数时不继续翻页。 */
    function goNext(): void {
        const page = query.data
        if (pagingBusy || !page || page.offset + page.limit >= page.total) return
        setOffset(page.offset + page.limit)
    }

    /** 当前页因数据变化变空时，回到第一页。 */
    function goFirst(): void {
        if (pagingBusy) return
        setOffset(0)
    }

    /** 按权限、查询结果和当前页内容选择需要显示的界面。 */
    function renderDocuments() {
        // 先判断权限，避免把禁用的查询误认为一直加载。
        if (!canManage) {
            return <p role="alert">当前账号没有文档管理权限，请使用主管或管理员账号。</p>
        }

        if (query.isPending) {
            return (
                <p role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后加载知识文档。'
                        : '正在加载知识文档……'}
                </p>
            )
        }

        if (query.isError) {
            return (
                <div className={styles.error}>
                    <p role="alert">
                        {errorStatus === 401
                            ? '登录已失效，请重新登录。'
                            : errorStatus === 403
                                ? '当前账号没有文档管理权限。'
                                : '暂时无法查询文档，请检查网络或后端服务后刷新。'}
                    </p>
                    {errorStatus === 401 && (
                        <button type="button" className={styles.button} onClick={signOut}>
                            重新登录
                        </button>
                    )}
                </div>
            )
        }

        const page = query.data
        const hasPrevious = page.offset > 0
        const hasNext = page.offset + page.limit < page.total

        return (
            <>
                <div className={styles.summary}>
                    <h2>文档目录</h2>
                    <p className={styles.muted}>
                        共 {page.total} 篇，
                        {page.items.length > 0
                            ? `当前显示第 ${page.offset + 1}–${page.offset + page.items.length} 篇。`
                            : '当前页显示 0 篇。'}
                    </p>
                </div>

                {/* 翻页时允许暂留旧数据，但明确说明它还不是新一页。 */}
                {query.isPlaceholderData && (
                    <p className={styles.notice} role="status">
                        {query.fetchStatus === 'paused'
                            ? '等待网络连接，暂时显示上一页文档。'
                            : '正在加载所选页，暂时显示上一页文档。'}
                    </p>
                )}

                {page.items.length === 0 ? (
                    <div className={styles.empty}>
                        <h3>{page.offset > 0 ? '当前页暂无文档' : '还没有知识文档'}</h3>
                        <p className={styles.muted}>
                            {page.offset > 0
                                ? '文档列表发生了变化，可以返回第一页重新查看。'
                                : '上传文件并完成入库后，文档会显示在这里。'}
                        </p>
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
                    <>
                        {/* 桌面使用表格；scope 说明每个表头对应一列。 */}
                        <table className={styles.table} aria-label="知识文档列表">
                            <thead>
                            <tr>
                                <th scope="col">文档</th>
                                <th scope="col">状态</th>
                                <th scope="col">范围</th>
                                <th scope="col">片段数</th>
                                <th scope="col">版本</th>
                                <th scope="col">更新时间（北京时间）</th>
                            </tr>
                            </thead>
                            <tbody>
                            {/* map 将每篇文档数据 item 转换为一行。 */}
                            {page.items.map((item) => (
                                <tr key={item.id}>
                                    <td>
                                        <strong className={styles.documentTitle}>
                                            {/* 把当前文档 ID 放进详情地址。 */}
                                            <Link
                                                className={styles.documentLink}
                                                to={`/knowledge/documents/${encodeURIComponent(item.id)}`}
                                            >
                                                {item.title}
                                            </Link>
                                        </strong>
                                        <span className={styles.source}>
                        {item.source_uri ?? (item.source_type === 'manual' ? '手动输入' : '未提供来源地址')}
                      </span>
                                    </td>
                                    <td>
                      <span className={styles.badge} data-status={item.status}>
                        {STATUS_LABELS[item.status]}
                      </span>
                                    </td>
                                    <td>
                      <span className={styles.visibility} data-visibility={item.visibility}>
                        {item.visibility === 'restricted' ? '受限可见' : '内部可见'}
                      </span>
                                    </td>
                                    <td>{item.chunk_count}</td>
                                    <td>v{item.version}</td>
                                    <td>{formatTime(item.updated_at)}</td>
                                </tr>
                            ))}
                            </tbody>
                        </table>

                        {/* 手机使用卡片；CSS 隐藏另一种布局，避免挤压表格。 */}
                        <ul className={styles.cards} aria-label="知识文档卡片列表">
                            {page.items.map((item) => (
                                <li key={item.id} className={styles.card}>
                                    <h3 className={styles.documentTitle}>
                                        {/* 手机卡片也提供相同的详情入口。 */}
                                        <Link
                                            className={styles.documentLink}
                                            to={`/knowledge/documents/${encodeURIComponent(item.id)}`}
                                        >
                                            {item.title}
                                        </Link>
                                    </h3>
                                    <p className={styles.source}>{item.source_uri ?? (item.source_type === 'manual' ? '手动输入' : '未提供来源地址')}</p>
                                    <div className={styles.badges}>
                    <span className={styles.badge} data-status={item.status}>
                      {STATUS_LABELS[item.status]}
                    </span>
                                        <span className={styles.visibility} data-visibility={item.visibility}>
                      {item.visibility === 'restricted' ? '受限可见' : '内部可见'}
                    </span>
                                    </div>
                                    <dl className={styles.cardFields}>
                                        <div>
                                            <dt>片段数</dt>
                                            <dd>{item.chunk_count}</dd>
                                        </div>
                                        <div>
                                            <dt>版本</dt>
                                            <dd>v{item.version}</dd>
                                        </div>
                                        <div className={styles.fullField}>
                                            <dt>更新时间（北京时间）</dt>
                                            <dd>{formatTime(item.updated_at)}</dd>
                                        </div>
                                    </dl>
                                </li>
                            ))}
                        </ul>
                    </>
                )}

                <nav className={styles.pagination} aria-label="知识文档分页">
                    <span className={styles.muted}>每页最多 {page.limit} 篇</span>
                    <div className={styles.actions}>
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
            </>
        )
    }

    return (
        <section>
            <header className={styles.header}>
                <div>
                    {/* 允许程序聚焦标题，同时不加入正常的 Tab 顺序。 */}
                    <h1 ref={titleRef} tabIndex={-1}>知识文档</h1>
                    <p className={styles.description}>查看文档状态、可见范围与当前版本。</p>
                </div>
                {showActions && (
                    <div className={styles.actions}>
                        <button
                            type="button"
                            className={styles.button}
                            aria-disabled={query.isFetching}
                            onClick={refreshDocuments}
                        >
                            {query.isFetching ? '刷新中……' : '刷新列表'}
                        </button>
                        <Link to="/knowledge/ingestion" className={styles.primaryButton}>
                            上传文档
                        </Link>
                    </div>
                )}
            </header>
            <div className={styles.panel}>{renderDocuments()}</div>
        </section>
    )
}
