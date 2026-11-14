/**
 * 文件职责：展示单篇知识文档的摘要和正文，提供归档确认及操作反馈。
 * 作用与配合：通过查询 Hook 加载文档，通过归档 Hook 提交操作并刷新缓存。
 * 使用位置：工作台的 /knowledge/documents/:documentId 路由。
 * 边界：正文按普通文字显示；归档权限和状态变更由后端检查与执行。
 */
import {useEffect, useRef, useState} from 'react'
import {Link, useParams} from 'react-router'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {useKnowledgeDocument} from '../hooks/useKnowledgeDocument'
// 管理当前文档的归档请求、提交状态和反馈。
import {useArchiveKnowledgeDocument} from '../hooks/useArchiveKnowledgeDocument'
import type {KnowledgeDocumentStatus, KnowledgeProcessingStage} from '../types'
import styles from './KnowledgeDocumentPage.module.css'
// 读取当前文档恢复操作的状态。
import {useRestoreKnowledgeDocument} from '../hooks/useRestoreKnowledgeDocument'
// 展示当前文档的历史版本记录和分页操作。
import {KnowledgeDocumentVersionsPanel} from '../components/KnowledgeDocumentVersionsPanel'
import {KnowledgeDocumentTitleEditor} from '../components/KnowledgeDocumentTitleEditor'

// Record 要求每一种状态都有对应的中文名称。
const STATUS_LABELS: Record<KnowledgeDocumentStatus, string> = {
    pending: '等待处理',
    processing: '正在处理',
    ready: '已就绪',
    failed: '处理失败',
    archived: '已归档',
}

const STAGE_LABELS: Record<KnowledgeProcessingStage, string> = {
    parsing: '解析文件（必要时 OCR）',
    cleaning: '清洗文字',
    chunking: '切分知识片段',
    embedding: '生成向量',
    indexing: '保存文档与向量',
}

// 按后端返回的来源类型，取得对应的中文名称。
const SOURCE_LABELS = {
    manual: '手动输入',
    file: '文件上传',
    url: '网页导入',
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

/** 组合文档正文、查询提示、刷新与归档确认操作。 */
export function KnowledgeDocumentPage() {
    // useParams 读取地址中 :documentId 对应的值。
    const {documentId} = useParams()
    const {canManage, hasValidDocumentId, query} = useKnowledgeDocument(documentId)
    // 原有的归档 Hook。
    const {archiveDocument, isArchiving, archiveMessage} =
        useArchiveKnowledgeDocument(documentId)

// 取得恢复方法、提交状态和结果提示。
    const {restoreDocument, isRestoring, restoreMessage} =
        useRestoreKnowledgeDocument(documentId)

    // 记录最近一次确认的是归档还是恢复。
// 类型限定只能保存这两个字符串。
    const [activeAction, setActiveAction] =
        useState<'archive' | 'restore'>('archive')

// 任意一种操作正在提交，都属于提交中。
    const isSubmitting = isArchiving || isRestoring

// 根据最近一次操作，选择对应的提示。
    const currentMessage =
        activeAction === 'restore' ? restoreMessage : archiveMessage

// 提交中显示进度文字，结束后显示操作结果。
    const operationMessage = isArchiving
        ? '正在归档文档，请稍候。'
        : isRestoring
            ? '正在恢复文档，请稍候。'
            : currentMessage

    // 立即锁住点击处理，防止提交状态更新前重复弹出确认。
    const operationClickRef = useRef(false)

    // 操作结束后聚焦反馈，避免归档按钮消失时键盘焦点丢失。
    const operationMessageRef = useRef<HTMLParagraphElement | null>(null)

    // 同时读取当前账号，用于区分编辑器所属的身份。
    const {session, signOut} = useAuth()
    const titleRef = useRef<HTMLHeadingElement | null>(null)

    // 进入另一篇文档时聚焦标题；刷新同一篇文档不移动焦点。
    useEffect(() => {
        const previousTitle = document.title
        document.title = '文档详情 · TicketPilot'
        titleRef.current?.focus()
        return () => {
            document.title = previousTitle
        }
    }, [documentId])

// 管理操作结束后，将焦点移到对应的结果提示。
    useEffect(() => {
        if (!isSubmitting && currentMessage) {
            operationMessageRef.current?.focus()
        }
    }, [currentMessage, isSubmitting])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined

    // fetching 是正在请求，paused 是等待网络；两种情况都不重复刷新。
    const refreshBusy = query.fetchStatus !== 'idle'

    // 查询或归档进行中时，等待当前操作完成。
    const operationBusy = isArchiving || isRestoring || refreshBusy

    // 只有成功读到当前文档且尚未归档，才显示归档入口。
    const canArchive =
        canManage &&
        hasValidDocumentId &&
        query.isSuccess &&
        query.data.id === documentId &&
        query.data.status !== 'archived'

    // 当前文档已经归档，并且用户有管理权限时，允许恢复。
    const canRestore =
        canManage &&
        hasValidDocumentId &&
        query.isSuccess &&
        query.data.id === documentId &&
        query.data.status === 'archived'

    // includes 判断状态码是否在数组中。
    // 身份、权限或文档不存在时，提供返回或重新登录入口。
    const showRefresh =
        canManage &&
        hasValidDocumentId &&
        ![401, 403, 404].includes(errorStatus ?? 0)

    /** 刷新当前文档，与归档和恢复共用立即锁。 */
    function refreshDocument(): void {
        if (!showRefresh || operationBusy || operationClickRef.current) return

        // 立即上锁，阻止状态重新渲染前启动其他操作。
        operationClickRef.current = true

        void query.refetch({cancelRefetch: false})
            // 查询错误由 Query 状态展示。
            .catch(() => undefined)
            .finally(() => {
                operationClickRef.current = false
            })
    }

    /**
     * 用户确认后执行归档或恢复。
     * 两种操作共用点击锁，防止重复确认或同时提交。
     */
    async function confirmDocumentAction(
        // archive 表示归档；restore 表示恢复。
        action: 'archive' | 'restore',
    ): Promise<void> {
        // 根据操作类型，选择对应的允许条件。
        const allowed = action === 'archive' ? canArchive : canRestore

        if (
            !allowed ||
            operationBusy ||
            operationClickRef.current ||
            !query.data
        ) return

        operationClickRef.current = true

        try {
            const item = query.data

            // 根据操作类型显示不同的确认文字。
            const confirmed = window.confirm(
                action === 'archive'
                    ? `确认归档“${item.title}”？\n\n归档后文档仍然保留，但停止参与正常知识检索。`
                    : `确认恢复“${item.title}”？\n\n恢复后文档将重新参与正常知识检索。`,
            )

            if (!confirmed) return

            // 确认后才切换结果提示的来源。
            setActiveAction(action)

            if (action === 'archive') {
                await archiveDocument(item.title)
            } else {
                await restoreDocument(item.title)
            }
        } finally {
            // 取消或请求结束，都释放点击锁。
            operationClickRef.current = false
        }
    }

    /** 根据权限、文档 ID 和查询结果，选择需要显示的内容。 */
    function renderDetails() {
        // 禁用查询时也可能处于 pending，先判断权限和 ID。
        if (!canManage) {
            return <p role="alert">当前账号没有文档管理权限，请使用主管或管理员账号。</p>
        }

        if (!hasValidDocumentId) {
            return <p role="alert">文档 ID 缺失或长度不正确，请返回文档列表。</p>
        }

        if (query.isPending) {
            return (
                <p role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后加载文档详情。'
                        : '正在加载文档详情……'}
                </p>
            )
        }

        // 错误先于缓存内容显示，避免把旧正文显示成最新查询结果。
        if (query.isError) {
            return (
                <div className={styles.error}>
                    <p role="alert">
                        {errorStatus === 401
                            ? '登录已失效，请重新登录。'
                            : errorStatus === 403
                                ? '当前账号没有查看这篇文档的权限。'
                                : errorStatus === 404
                                    ? '知识文档不存在，请返回文档列表。'
                                    : '暂时无法获取文档详情，请检查网络或后端服务后刷新。'}
                    </p>
                    {errorStatus === 401 && (
                        <button type="button" className={styles.button} onClick={signOut}>
                            重新登录
                        </button>
                    )}
                </div>
            )
        }

        // item 是后端返回的文档详情，避免与浏览器的 document 对象同名。
        const item = query.data

        // 每组数据分别保存字段名称和准备展示的值。
        // 先展示阅读文档常用的信息，更多处理细节放在后面的折叠区域。
        const fields = [
            ['原始文件或来源', item.source_uri ?? (item.source_type === 'manual' ? '手动输入' : '未提供来源地址')],
            ['当前版本', `v${item.version}`],
            ['知识片段数', item.chunk_count],
            ['更新时间（北京时间）', formatTime(item.updated_at)],
        ]

        const processingFields = [
            ['文档 ID', item.id],
            ['来源类型', SOURCE_LABELS[item.source_type]],
            ['处理阶段', item.processing_stage ? STAGE_LABELS[item.processing_stage] : '暂无阶段记录'],
            ['向量模型', item.embedding_model],
            ['向量维度', item.embedding_dimensions],
            ['清洗算法版本', item.cleaner_version],
            ['切块算法版本', item.chunker_version],
            ['创建时间（北京时间）', formatTime(item.created_at)],
        ]

        return (
            <>
                <div className={styles.summary}>
                    <h2>{item.title}</h2>
                    <div className={styles.badges}>
            <span className={styles.badge} data-status={item.status}>
              {STATUS_LABELS[item.status]}
            </span>
                        <span className={styles.visibility} data-visibility={item.visibility}>
              {item.visibility === 'restricted' ? '受限可见' : '内部可见'}
            </span>
                    </div>
                </div>

                {/* dl 表示名称与值的列表；dt 是名称，dd 是对应的值。 */}
                <dl className={styles.fields}>
                    {fields.map(([label, value]) => (
                        <div key={label} className={styles.field}>
                            <dt>{label}</dt>
                            <dd>{value}</dd>
                        </div>
                    ))}
                </dl>

                <section className={styles.contentSection} aria-labelledby="cleaned-content-heading">
                    <h2 id="cleaned-content-heading">清洗后的正文</h2>
                    <p className={styles.contentHint}>用于后续切块与检索的文字内容。</p>
                    {/* 普通 JSX 文本会转义 HTML；样式负责保留换行和空格。 */}
                    <div className={styles.documentText}>
                        {item.cleaned_content || '暂无清洗后的正文。'}
                    </div>
                </section>

                {/* details 和 summary 是浏览器原生折叠区域，支持鼠标和键盘。 */}
                {/* key 改变时重建这个区域，切换文档后默认重新折叠。 */}
                <details className={styles.disclosure} key={documentId}>
                    <summary>查看原始正文（清洗前）</summary>
                    <p className={styles.contentHint}>文件解析得到的文字，或手动提交的原始文字。</p>
                    <div className={styles.documentText}>
                        {item.raw_content || '暂无原始正文。'}
                    </div>
                </details>

                <details className={styles.disclosure} key={`processing-${documentId}`}>
                    <summary>查看处理信息</summary>
                    <dl className={styles.fields}>
                        {processingFields.map(([label, value]) => (
                            <div key={label} className={styles.field}>
                                <dt>{label}</dt>
                                <dd>{value}</dd>
                            </div>
                        ))}
                    </dl>
                </details>
                {/* 切换文档时重建组件，让历史版本分页回到第一页。 */}
                <KnowledgeDocumentVersionsPanel
                    key={item.id}
                    documentId={item.id}
                />
            </>
        )
    }

    return (
        <section className={styles.page}>
            <header className={styles.header}>
                <div>
                    {/* 允许程序聚焦标题，不加入正常的 Tab 顺序。 */}
                    <h1 ref={titleRef} tabIndex={-1}>文档详情</h1>
                    <p className={styles.description}>查看文档信息与正文，核对入库结果。</p>
                </div>
                <div className={styles.actions}>
                    <Link to="/knowledge/documents" className={styles.button}>
                        返回文档列表
                    </Link>
                    {showRefresh && (
                        <button
                            type="button"
                            className={styles.button}
                            aria-disabled={operationBusy}
                            onClick={refreshDocument}
                        >
                            {query.fetchStatus === 'paused'
                                ? '等待网络……'
                                : query.isFetching ? '刷新中……' : '刷新文档'}
                        </button>
                    )}
                    {/* 尚未归档的文档提供归档入口。 */}
                    {canArchive && (
                        <button
                            type="button"
                            className={`${styles.button} ${styles.archiveButton}`}
                            aria-disabled={operationBusy}
                            onClick={() => {
                                void confirmDocumentAction('archive')
                            }}
                        >
                            {isArchiving ? '归档中……' : '归档文档'}
                        </button>
                    )}
                    {/* 已归档文档显示恢复按钮。 */}
                    {canRestore && (
                        <button
                            type="button"
                            className={`${styles.button} ${styles.restoreButton}`}
                            aria-disabled={operationBusy}
                            onClick={() => {
                                void confirmDocumentAction('restore')
                            }}
                        >
                            {isRestoring ? '恢复中……' : '恢复文档'}
                        </button>
                    )}
                </div>
            </header>

            {/* 持续保留状态区域，方便读屏软件播报文字变化。 */}
            <div role="status" aria-live="polite" aria-atomic="true">
                {operationMessage && (
                    <p
                        ref={operationMessageRef}
                        tabIndex={-1}
                        className={styles.operationNotice}
                    >
                        {operationMessage}
                    </p>
                )}
            </div>
            {refreshBusy && query.data && !query.isError && (
                <p className={styles.refreshNotice} role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后刷新当前文档。'
                        : '正在刷新当前文档内容……'}
                </p>
            )}
            {/* 加载提示本身通过 role=status 播报，不用 aria-busy 把它包住。 */}
            <div className={styles.card}>
                {canManage && hasValidDocumentId && documentId && (
                    <KnowledgeDocumentTitleEditor
                        key={JSON.stringify([
                            documentId,          // 换文档时重建。
                            session?.user.id,    // 换账号时重建。
                            session?.user.role,  // 角色改变时重建。
                        ])}
                        documentId={documentId}
                        canEdit={
                            query.isSuccess &&
                            !operationBusy &&
                            query.data.id === documentId &&
                            query.data.status === 'ready'
                        }
                    />
                )}

                {renderDetails()}
            </div>
        </section>
    )
}
