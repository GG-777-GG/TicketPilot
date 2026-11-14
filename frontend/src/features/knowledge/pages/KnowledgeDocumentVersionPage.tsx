/**
 * 文件职责：展示当前文档信息与正文，组合标题编辑和归档恢复操作。
 * 作用与配合：查询文档，组合编辑组件、历史版本列表和管理操作。
 * 使用位置：/knowledge/documents/:documentId 路由。
 * 边界：正文按普通文字显示；实际权限、保存和状态检查由后端执行。
 */
import {useEffect, useRef} from 'react'
import {Link, useParams} from 'react-router'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {useKnowledgeDocumentVersion} from '../hooks/useKnowledgeDocumentVersion'
import styles from './KnowledgeDocumentVersionPage.module.css'
import {KnowledgeDocumentVersionRestorePanel} from '../components/KnowledgeDocumentVersionRestorePanel'


/** 将后端的 ISO 时间字符串显示为北京时间。 */
function formatTime(
    // 历史版本保存的时间字段。
    value: string,
): string {
    return new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
    })
}

/** 组合历史正文、查询状态、刷新与返回操作。 */
export function KnowledgeDocumentVersionPage() {
    // 路由参数是字符串，需要先把版本号转换成数字。
    const params = useParams()
    const documentId = params.documentId
    const versionText = params.version
    const version = versionText === undefined ? undefined : Number(versionText)
    const {canManage, hasValidDocumentId, hasValidVersion, query} =
        useKnowledgeDocumentVersion(documentId, version)
    const {session, signOut} = useAuth()
    const titleRef = useRef<HTMLHeadingElement | null>(null)
    const clickRef = useRef(false)

    // 切换文档或版本时聚焦标题，刷新同一份正文时保持原焦点。
    useEffect(() => {
        const previousTitle = document.title
        document.title = '历史版本详情 · TicketPilot'
        titleRef.current?.focus()
        return () => {
            document.title = previousTitle
        }
    }, [documentId, versionText])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined
    // 请求中和等待网络时，都不重复刷新。
    const busy = query.fetchStatus !== 'idle'
    const showRefresh =
        canManage && hasValidDocumentId && hasValidVersion &&
        ![401, 403, 404].includes(errorStatus ?? 0)

    /** 手动刷新当前历史版本，立即锁防止快速重复点击。 */
    function refreshVersion(): void {
        if (!showRefresh || busy || clickRef.current) return
        clickRef.current = true
        void query.refetch({cancelRefetch: false})
            .catch(() => undefined)
            .finally(() => {
                clickRef.current = false
            })
    }

    /** 按权限、参数和查询结果，选择需要显示的历史内容。 */
    function renderDetails() {
        if (!canManage) return <p role="alert">当前账号没有查看历史版本的权限。</p>
        if (!hasValidDocumentId) return <p role="alert">文档 ID 无效，请返回文档列表。</p>
        if (!hasValidVersion) return <p role="alert">版本号必须是大于等于 1 的安全整数，请返回当前文档选择历史版本。</p>

        if (query.isPending) {
            return <p role="status">
                {query.fetchStatus === 'paused'
                    ? '等待网络连接后加载历史正文。'
                    : '正在加载历史版本正文……'}
            </p>
        }

        // 查询失败时优先显示错误，不把缓存正文冒充最新查询结果。
        if (query.isError) {
            return (
                <div className={styles.error}>
                    <p role="alert">
                        {errorStatus === 401 ? '登录已失效，请重新登录。'
                            : errorStatus === 403 ? '当前账号没有查看历史版本的权限。'
                                : errorStatus === 404 ? '未找到指定历史版本，请返回当前文档核对。'
                                    : '暂时无法获取历史正文，请检查网络或后端服务后刷新。'}
                    </p>
                    {errorStatus === 401 && (
                        <button type="button" className={styles.button} onClick={signOut}>
                            重新登录
                        </button>
                    )}
                </div>
            )
        }

        const item = query.data
        const fields = [
            ['历史版本号', `v${item.version}`],
            ['原始文件或来源', item.source_uri ?? (item.source_type === 'manual' ? '手动输入' : '未提供来源地址')],
            ['版本创建时间（北京时间）', formatTime(item.created_at)],
            ['被替换时间（北京时间）', formatTime(item.superseded_at)],
        ]

        return (
            <>
                <div className={styles.summary}>
                    <h2>{item.title}</h2>
                    <span className={styles.badge}>历史快照 · v{item.version}</span>
                </div>
                <dl className={styles.fields}>
                    {fields.map(([label, value]) => (
                        <div key={label} className={styles.field}>
                            <dt>{label}</dt>
                            <dd>{value}</dd>
                        </div>
                    ))}
                </dl>
                <section className={styles.content} aria-labelledby="historical-cleaned-heading">
                    <h2 id="historical-cleaned-heading">清洗后的正文（历史版本）</h2>
                    {/* 普通 JSX 文本会转义 HTML，样式负责保留换行。 */}
                    <div className={styles.documentText}>
                        {item.cleaned_content || '这个历史版本暂无清洗后的正文。'}
                    </div>
                    {/* 历史正文加载成功后，才显示对应版本的恢复入口。 */}
                    {documentId && version !== undefined && (
                        <KnowledgeDocumentVersionRestorePanel
                            key={JSON.stringify([documentId, version, session?.user.id, session?.user.role])}
                            documentId={documentId}
                            version={version}
                            historicalTitle={item.title}
                        />
                    )}
                </section>
                {/* 切换历史版本时，重新折叠原始正文。 */}
                <details className={styles.disclosure} key={`${documentId}-${version}`}>
                    <summary>查看原始正文（历史版本）</summary>
                    <div className={styles.documentText}>
                        {item.raw_content || '这个历史版本暂无原始正文。'}
                    </div>
                </details>
            </>
        )
    }

    return (
        <section className={styles.page}>
            <header className={styles.header}>
                <div>
                    <h1 ref={titleRef} tabIndex={-1}>历史版本详情</h1>
                    <p className={styles.muted}>查看文档在指定历史版本中保存的内容。</p>
                </div>
                <div className={styles.actions}>
                    <Link to="/knowledge/documents" className={styles.button}>返回文档列表</Link>
                    {hasValidDocumentId && documentId && (
                        <Link className={styles.button}
                              to={`/knowledge/documents/${encodeURIComponent(documentId)}`}>
                            返回当前文档
                        </Link>
                    )}
                    {showRefresh && (
                        <button type="button" className={styles.button}
                                aria-disabled={busy} onClick={refreshVersion}>
                            {busy ? '等待查询完成……' : '刷新历史正文'}
                        </button>
                    )}
                </div>
            </header>
            {busy && query.data && !query.isError && (
                <p className={styles.notice} role="status">
                    {query.fetchStatus === 'paused'
                        ? '等待网络连接后刷新历史正文。'
                        : '正在刷新当前历史版本正文……'}
                </p>
            )}
             <div className={styles.card}>
                {renderDetails()}
            </div>
        </section>
    )
}
