/**
 * 文件职责：展示单张工单的完整信息、问题描述及查询状态。
 * 作用与配合：通过 useParams 读取地址参数，通过 useTicket 查询。
 * 使用位置：工作台的 /tickets/:ticketId 路由。
 * 边界：组合详情、状态修改、处理人分配及删除确认，不直接发送写请求。
 */
import {useEffect, useRef} from 'react'
import {Link, useParams} from 'react-router'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {useTicket} from '../hooks/useTicket'
import { STATUS_LABELS, PRIORITY_LABELS, CHANNEL_LABELS, formatTicketTime as formatTime } from '../display'
import styles from './TicketPage.module.css'
import {useUpdateTicketStatus} from '../hooks/useUpdateTicketStatus'
import {TicketStatusEditor} from '../components/TicketStatusEditor'
import {useUpdateTicketAssignee} from '../hooks/useUpdateTicketAssignee'
import {TicketAssigneeEditor} from '../components/TicketAssigneeEditor'
import {useDeleteTicket} from '../hooks/useDeleteTicket'
import {TicketDeletePanel} from '../components/TicketDeletePanel'

/** 读取地址参数，展示当前工单及刷新、返回操作。 */
export function TicketPage() {
    // 名称需要与路由中的 :ticketId 一致。
    const {ticketId} = useParams<{ ticketId: string }>()
    const {canRead, hasTicketId, query} = useTicket(ticketId)
    const {session, signOut} = useAuth()

    const {
        canUpdate,
        isSaving,
        saveMessage,
        saveHttpStatus,
        updateStatus,
    } = useUpdateTicketStatus(ticketId)
    const {
        canUpdate: canAssign,
        isSaving: isAssigning,
        saveMessage: assigneeMessage,
        saveHttpStatus: assigneeHttpStatus,
        updateAssignee,
    } = useUpdateTicketAssignee(ticketId)
    const {
        canManage, canDelete, isDeleting, deleteMessage,
        deleteHttpStatus, deleteOutcome, deleteConfirmed,
    } = useDeleteTicket(ticketId)
    const deleteResultRef = useRef<HTMLParagraphElement | null>(null)
    const titleRef = useRef<HTMLHeadingElement | null>(null)

    // 进入另一张工单时，更新浏览器标题并聚焦页面标题。
    useEffect(() => {
        const previousTitle = document.title
        document.title = '工单详情 · TicketPilot'
        titleRef.current?.focus()

        return () => {
            document.title = previousTitle
        }
    }, [ticketId])

    // 删除流程结束后，聚焦独立于详情查询的结果提示。
    useEffect(() => {
        if (deleteMessage && !isDeleting) {
            deleteResultRef.current?.focus()
        }
    }, [deleteMessage, isDeleting])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined

    // fetching 表示请求中，paused 表示暂时等待网络。
    // 登录、权限或工单不存在的问题，不通过反复提交解决。
    // 任一写请求遇到身份、权限或工单不存在的问题，都暂停编辑。
    const saveBlocked =
        saveHttpStatus === 401 ||
        saveHttpStatus === 403 ||
        saveHttpStatus === 404 ||
        assigneeHttpStatus === 401 ||
        assigneeHttpStatus === 403 ||
        assigneeHttpStatus === 404 ||
        deleteOutcome !== 'idle'

// 保存期间也暂停手动刷新和编辑操作。
    // 查询、状态保存、处理人保存，共同控制两个编辑区。
    const busy =
        query.fetchStatus !== 'idle' ||
        isSaving ||
        isAssigning ||
        isDeleting
    const canRefresh =
        canRead &&
        hasTicketId &&
        errorStatus !== 401 &&
        errorStatus !== 403 &&
        deleteHttpStatus !== 401 &&
        deleteHttpStatus !== 403 &&
        deleteOutcome !== 'deleted' &&
        deleteOutcome !== 'missing'

    /** 重新查询当前工单，已有请求时不重复发起。 */
    function refreshTicket(): void {
        if (!canRefresh || busy) return
        void query.refetch({cancelRefetch: false})
    }

    /** 重新读取成功后，通知编辑组件解除提交限制。 */
    async function reloadStatus(): Promise<boolean> {
        if (!canRefresh || busy) return false

        const result = await query.refetch({cancelRefetch: false})

        return result.isSuccess && result.data.id === ticketId
    }

    /** 根据身份、参数和查询结果选择展示内容。 */
    function renderDetails() {
        if (!canRead) {
            return <p role="alert">请先登录再查看工单。</p>
        }

        if (!hasTicketId) {
            return <p role="alert">页面地址缺少工单 ID。</p>
        }

        if (deleteOutcome === 'deleted' || deleteOutcome === 'missing') {
            return <p>此工单已不存在，请返回工单列表。</p>
        }

        if (query.isPending) {
            return (
                <p role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后加载工单。'
                        : '正在加载工单详情……'}
                </p>
            )
        }

        // 错误优先显示，避免展示刷新失败前的旧详情。
        if (query.isError) {
            const message =
                errorStatus === 401
                    ? '登录已失效，请重新登录。'
                    : errorStatus === 403
                        ? '当前账号无法访问这张工单。'
                        : errorStatus === 404
                            ? '工单不存在或已被删除，请返回列表查看。'
                            : '暂时无法加载工单，请检查网络或后端服务后刷新。'

            return (
                <div>
                    <p role="alert" className={styles.error}>
                        {message}
                    </p>
                    {errorStatus === 401 && (
                        <button
                            type="button"
                            className={styles.button}
                            onClick={signOut}
                        >
                            重新登录
                        </button>
                    )}
                </div>
            )
        }

        const ticket = query.data

        return (
            <>
                {query.fetchStatus !== 'idle' && (
                    <p role="status" className={styles.muted}>
                        {query.fetchStatus === 'paused'
                            ? '等待网络连接，暂时显示上次加载的详情。'
                            : '正在刷新，暂时显示上次加载的详情。'}
                    </p>
                )}

                <h2 className={styles.subject}>{ticket.subject}</h2>
                <p className={styles.muted}>工单 ID：{ticket.id}</p>

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
                        <dt>状态</dt>
                        <dd>{STATUS_LABELS[ticket.status]}</dd>
                    </div>
                    <div>
                        <dt>优先级</dt>
                        <dd>{PRIORITY_LABELS[ticket.priority]}</dd>
                    </div>
                    <div>
                        <dt>处理人</dt>
                        <dd>{ticket.assigned_to ?? '未分配'}</dd>
                    </div>
                    <div>
                        <dt>创建时间（北京时间）</dt>
                        <dd>{formatTime(ticket.created_at)}</dd>
                    </div>
                    <div>
                        <dt>更新时间（北京时间）</dt>
                        <dd>{formatTime(ticket.updated_at)}</dd>
                    </div>
                </dl>

                <section className={styles.description}>
                    <h2>问题描述</h2>
                    {/* 直接按文本显示，保留换行，不把内容作为 HTML 执行。 */}
                    <p className={styles.body}>{ticket.description}</p>
                </section>
            </>
        )
    }

    return (
        <section>
            <header className={styles.header}>
                <h1 ref={titleRef} tabIndex={-1}>工单详情</h1>

                <div className={styles.actions}>
                    {/* 固定返回列表，直接打开详情地址时也能正常使用。 */}
                    <Link to="/tickets" className={styles.button}>
                        返回工单列表
                    </Link>

                    {canRefresh && (
                        <button
                            type="button"
                            className={styles.button}
                            aria-disabled={busy}
                            onClick={refreshTicket}
                        >
                            {busy ? '操作进行中……' : '刷新详情'}
                        </button>
                    )}
                </div>
            </header>

            <div className={styles.panel} aria-busy={busy}>
                {renderDetails()}

                {/* 编辑区保持挂载，查询失败时仍保留草稿与保存提示。 */}
                {canRead && hasTicketId && (
                    <>
                        {isSaving && (
                            <p role="status">正在保存并核对状态……</p>
                        )}

                        {saveMessage && (
                            <p role="status">{saveMessage}</p>
                        )}

                        {(saveHttpStatus === 401 || assigneeHttpStatus === 401 || deleteHttpStatus === 401) && (
                            <button
                                type="button"
                                className={styles.button}
                                onClick={signOut}
                            >
                                重新登录
                            </button>
                        )}

                        <TicketStatusEditor
                            key={JSON.stringify([
                                ticketId,
                                session?.user.id,
                                session?.user.role,
                            ])}
                            currentStatus={
                                query.isSuccess && query.data.id === ticketId
                                    ? query.data.status
                                    : undefined
                            }
                            canEdit={
                                canUpdate &&
                                !saveBlocked &&
                                query.isSuccess &&
                                query.data.id === ticketId
                            }
                            canReload={canRefresh && !saveBlocked}
                            busy={busy}
                            onSave={updateStatus}
                            onReload={reloadStatus}
                        />
                        {isAssigning && (
                            <p role="status">正在保存并核对处理人……</p>
                        )}

                        {assigneeMessage && (
                            <p role="status">{assigneeMessage}</p>
                        )}

                        <TicketAssigneeEditor
                            key={JSON.stringify([
                                'assignee',
                                ticketId,
                                session?.user.id,
                                session?.user.role,
                            ])}
                            currentAssignee={
                                query.isSuccess && query.data.id === ticketId
                                    ? query.data.assigned_to
                                    : undefined
                            }
                            canEdit={
                                canAssign &&
                                !saveBlocked &&
                                query.isSuccess &&
                                query.data.id === ticketId
                            }
                            canReload={canRefresh && !saveBlocked}
                            busy={busy}
                            onSave={updateAssignee}
                            onReload={reloadStatus}
                        />

                        {/* 保持删除区挂载，删除后的详情 404 不会清掉结果。 */}
                        {canManage && (
                            <>
                                {isDeleting && <p role="status">正在删除并核对结果……</p>}
                                <p ref={deleteResultRef} tabIndex={-1} role="status">
                                    {deleteMessage}
                                </p>
                                <TicketDeletePanel
                                    key={JSON.stringify([
                                        'delete', ticketId,
                                        session?.user.id, session?.user.role,
                                    ])}
                                    target={
                                        query.isSuccess && query.data.id === ticketId
                                            ? { id: query.data.id, subject: query.data.subject }
                                            : undefined
                                    }
                                    canDelete={
                                        canDelete && !saveBlocked &&
                                        query.isSuccess && query.data.id === ticketId
                                    }
                                    busy={busy}
                                    onConfirm={deleteConfirmed}
                                    onCloseResult={() => {
                                        const target = deleteMessage
                                            ? deleteResultRef.current
                                            : titleRef.current
                                        target?.focus()
                                    }}
                                />
                            </>
                        )}
                    </>
                )}
            </div>
        </section>
    )
}
