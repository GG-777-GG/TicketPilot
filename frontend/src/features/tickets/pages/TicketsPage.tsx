/**
 * 文件职责：展示工单列表、加载和错误提示，提供分页与刷新。
 * 作用与配合：调用 useTickets，按登录身份隔离分页状态。
 * 使用位置：工作台的 /tickets 路由。
 * 边界：展示列表并提供详情入口，不修改工单。
 */
import {useEffect, useRef, useState} from 'react'
import {Link} from 'react-router'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {TICKET_PAGE_SIZE, useTickets} from '../hooks/useTickets'
import { STATUS_LABELS, PRIORITY_LABELS, CHANNEL_LABELS, formatTicketTime as formatTime } from '../display'
import styles from './TicketsPage.module.css'

/** 按账号和角色建立独立的列表组件，切换身份时重置分页。 */
export function TicketsPage() {
    const {session, status} = useAuth()

    if (status !== 'authenticated' || !session) {
        return <p role="alert">请先登录再查看工单。</p>
    }

    return (
        <TicketsList
            key={JSON.stringify([session.user.id, session.user.role])}
        />
    )
}

/** 保存页码，组合当前页的展示与操作。 */
function TicketsList() {
    const [pageIndex, setPageIndex] = useState(0)
    const {canRead, query} = useTickets(pageIndex)
    const {signOut} = useAuth()
    const titleRef = useRef<HTMLHeadingElement | null>(null)

    useEffect(() => {
        const previousTitle = document.title
        document.title = '工单列表 · TicketPilot'
        titleRef.current?.focus()
        return () => {
            document.title = previousTitle
        }
    }, [])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined
    const accessDenied = errorStatus === 401 || errorStatus === 403
    const busy = query.fetchStatus !== 'idle'
    const canOperate = canRead && !accessDenied

    // 失败时不把缓存结果当作当前成功结果展示。
    const page = query.isSuccess ? query.data : undefined
    const canPrevious = canOperate && !busy && pageIndex > 0
    const canNext = canOperate && !busy && page?.hasNext === true

    /** 手动刷新当前页；已有请求或离线等待时不重复发起。 */
    function refreshTickets(): void {
        if (!canOperate || busy) return
        void query.refetch({cancelRefetch: false})
    }

    /** 每次回退一页；页码始终不小于 0。 */
    function goPrevious(): void {
        if (!canPrevious) return
        setPageIndex(Math.max(0, pageIndex - 1))
    }

    /** 只有本次成功响应证实还有数据时，才进入下一页。 */
    function goNext(): void {
        if (!canNext) return
        setPageIndex(pageIndex + 1)
    }

    /** 数据变化导致后面的页变空时，允许回到第一页。 */
    function goFirst(): void {
        if (!canOperate || busy) return
        setPageIndex(0)
    }

    /** 根据查询状态显示提示或工单卡片。 */
    function renderTickets() {
        if (!canRead) {
            return <p role="alert">请先登录再查看工单。</p>
        }

        if (query.isPending) {
            return (
                <p role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后加载工单。'
                        : '正在加载工单……'}
                </p>
            )
        }

        if (query.isError) {
            return (
                <div>
                    <p role="alert" className={styles.error}>
                        {errorStatus === 401
                            ? '登录已失效，请重新登录。'
                            : errorStatus === 403
                                ? '当前账号无法访问工单，请联系管理员。'
                                : '暂时无法查询工单，请检查网络或后端服务后刷新。'}
                    </p>
                    {errorStatus === 401 && (
                        <button type="button" className={styles.button} onClick={signOut}>
                            重新登录
                        </button>
                    )}
                </div>
            )
        }

        if (!page || page.items.length === 0) {
            return (
                <div>
                    <p>{pageIndex === 0 ? '暂时没有工单。' : '当前页暂无工单。'}</p>
                    {pageIndex > 0 && (
                        <button
                            type="button"
                            className={styles.button}
                            aria-disabled={busy}
                            onClick={goFirst}
                        >
                            返回第一页
                        </button>
                    )}
                </div>
            )
        }

        return (
            <ul className={styles.list} aria-label="工单列表">
                {page.items.map((ticket) => (
                    <li key={ticket.id} className={styles.card}>
                        <h2 className={styles.subject}>
                            {/* 点击工单标题，进入对应的详情页。 */}
                            <Link
                                className={styles.ticketLink}
                                to={'/tickets/' + encodeURIComponent(ticket.id)}
                            >
                                {ticket.subject}
                            </Link>
                        </h2>
                        <p className={styles.muted}>工单 ID：{ticket.id}</p>

                        <div className={styles.badges}>
                            <span className={styles.badge} data-status={ticket.status}>
                                {STATUS_LABELS[ticket.status]}
                            </span>
                            <span className={styles.badge} data-priority={ticket.priority}>
                                {PRIORITY_LABELS[ticket.priority] + '优先级'}
                            </span>
                        </div>

                        <dl className={styles.fields}>
                            <div>
                                <dt>客户</dt>
                                <dd>{ticket.customer_name}</dd>
                            </div>
                            <div>
                                <dt>联系邮箱</dt>
                                <dd>{ticket.contact_email}</dd>
                            </div>
                            <div>
                                <dt>渠道</dt>
                                <dd>{CHANNEL_LABELS[ticket.channel]}</dd>
                            </div>
                            <div>
                                <dt>处理人</dt>
                                <dd>{ticket.assigned_to ?? '未分配'}</dd>
                            </div>
                            <div>
                                <dt>更新时间（北京时间）</dt>
                                <dd>{formatTime(ticket.updated_at)}</dd>
                            </div>
                        </dl>
                    </li>
                ))}
            </ul>
        )
    }

    return (
        <section>
            <header className={styles.header}>
                <div>
                    <h1 ref={titleRef} tabIndex={-1}>工单列表</h1>
                    <p className={styles.muted}>按创建时间从新到旧查看客户问题。</p>
                </div>
                {canOperate && (
                    <div className={styles.actions}>
                        <button
                            type="button"
                            className={styles.button}
                            aria-disabled={busy}
                            onClick={refreshTickets}
                        >
                            {busy ? '等待查询结果……' : '刷新列表'}
                        </button>

                        <Link to="/tickets/new" className={styles.createLink}>
                            新建工单
                        </Link>
                    </div>
                )}
            </header>

            <div className={styles.panel} aria-busy={busy}>
                {page && busy && (
                    <p role="status" className={styles.muted}>
                        {query.fetchStatus === 'paused'
                            ? '等待网络连接，暂时显示上次加载的当前页工单。'
                            : '正在刷新，暂时显示上次加载的当前页工单。'}
                    </p>
                )}

                {renderTickets()}

                {canOperate && (
                    <nav className={styles.pagination} aria-label="工单分页">
                        <p className={styles.muted} role="status">
                            第 {pageIndex + 1} 页，每页最多 {TICKET_PAGE_SIZE} 条
                            {page ? '，当前显示 ' + page.items.length + ' 条。' : '。'}
                        </p>
                        <div className={styles.actions}>
                            <button
                                type="button"
                                className={styles.button}
                                aria-disabled={!canPrevious}
                                onClick={goPrevious}
                            >
                                上一页
                            </button>
                            <button
                                type="button"
                                className={styles.button}
                                aria-disabled={!canNext}
                                onClick={goNext}
                            >
                                下一页
                            </button>
                        </div>
                    </nav>
                )}
            </div>
        </section>
    )
}
