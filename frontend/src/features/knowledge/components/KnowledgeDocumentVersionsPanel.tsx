/**
 * 文件职责：展示一篇知识文档的历史版本摘要、查询提示和分页操作。
 * 作用与配合：调用 useKnowledgeDocumentVersions；由文档详情页传入 documentId。
 * 使用位置：嵌入文档详情页，父级使用 key={documentId} 重置不同文档的分页。
 * * 边界：只读展示历史记录并提供正文查看入口，不执行版本恢复。
 */
import {useEffect, useRef, useState} from 'react'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {useKnowledgeDocumentVersions} from '../hooks/useKnowledgeDocumentVersions'
import styles from './KnowledgeDocumentVersionsPanel.module.css'
import {Link} from 'react-router'
// 每页最多查询 10 条历史记录。
const PAGE_SIZE = 10

/** 父页面传入的组件参数。 */
type KnowledgeDocumentVersionsPanelProps = {
    // 当前知识文档 ID。
    documentId: string
}

/** 将历史版本被替换的时间显示为北京时间。 */
function formatTime(
    // 后端返回的 ISO 时间字符串。
    value: string,
): string {
    return new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
    })
}

/** 组合历史版本卡片、刷新、分页与查询反馈。 */
export function KnowledgeDocumentVersionsPanel({
    documentId,
}: KnowledgeDocumentVersionsPanelProps) {
    const [offset, setOffset] = useState(0)
    const {canManage, hasValidDocumentId, query} =
        useKnowledgeDocumentVersions(documentId, {limit: PAGE_SIZE, offset})
    const {signOut} = useAuth()
    // 立即阻止同一轮渲染内重复刷新或同时翻页。
    const clickRef = useRef(false)

    // 分页状态提交后释放立即锁，请求中的限制继续由 busy 控制。
    useEffect(() => {
        clickRef.current = false
    }, [offset])

    const errorStatus =
        query.error instanceof ApiError ? query.error.status : undefined
    // paused 表示等待网络，也需要阻止重复操作。
    const busy = query.fetchStatus !== 'idle'
    const showActions =
        canManage && hasValidDocumentId &&
        ![401, 403, 404].includes(errorStatus ?? 0)
    // 查询失败时不把已有缓存当作最新的成功结果展示。
    const page = query.isSuccess ? query.data : undefined
    const hasNext = !!page && page.offset + page.limit < page.total

    // 这一段文字始终放在同一个状态区域，便于播报变化。
    let statusMessage = ''
    if (query.isPending || (busy && page)) {
        statusMessage = query.fetchStatus === 'paused'
            ? '等待网络连接后加载历史版本。'
            : '正在加载历史版本……'
    } else if (page) {
        statusMessage = `当前版本 v${page.current_version}，共 ${page.total} 条历史记录。`
        if (page.items.length > 0) {
            statusMessage += `当前显示第 ${page.offset + 1}–${page.offset + page.items.length} 条。`
        }
    }

    /** 刷新当前历史版本页，不重复发起正在进行的请求。 */
    function refreshVersions(): void {
        if (!showActions || busy || clickRef.current) return
        clickRef.current = true
        void query.refetch({cancelRefetch: false})
            .catch(() => undefined)
            .finally(() => { clickRef.current = false })
    }

    /** 切换分页位置；允许查询失败后返回前面的页。 */
    function changePage(
        // 下一次查询准备跳过的历史记录数。
        nextOffset: number,
    ): void {
        if (
            !showActions || busy || clickRef.current ||
            nextOffset < 0 || nextOffset === offset ||
            (nextOffset > offset && !hasNext)
        ) return
        clickRef.current = true
        setOffset(nextOffset)
    }

    /** 按权限、加载、错误和成功结果展示历史记录。 */
    function renderVersions() {
        if (!canManage) return <p role="alert">当前账号没有查看历史版本的权限。</p>
        if (!hasValidDocumentId) return <p role="alert">文档 ID 无效，请返回文档列表。</p>
        // 加载文字已经由外面的固定状态区域展示。
        if (query.isPending) return null
        if (query.isError) {
            return (
                <div>
                    <p role="alert">
                        {errorStatus === 401 ? '登录已失效，请重新登录。'
                            : errorStatus === 403 ? '当前账号没有查看历史版本的权限。'
                                : errorStatus === 404 ? '知识文档不存在，请返回文档列表。'
                                    : '暂时无法查询历史版本，请检查网络或后端服务后刷新。'}
                    </p>
                    {errorStatus === 401 && (
                        <button type="button" className={styles.button} onClick={signOut}>
                            重新登录
                        </button>
                    )}
                </div>
            )
        }
        if (query.data.items.length === 0) {
            return <p className={styles.muted}>
                {offset > 0 ? '当前页暂无历史记录，可以返回第一页。'
                    : '暂无历史版本。文档内容更新后，被替换的旧版本会显示在这里。'}
            </p>
        }
        return (
            <ul className={styles.list} aria-label="历史版本记录">
                {query.data.items.map((item) => (
                    <li key={item.version} className={styles.item}>
                        <h3><span className={styles.version}>v{item.version}</span> {item.title}</h3>
                        <p className={styles.muted}>
                            来源：{item.source_uri ?? (item.source_type === 'manual' ? '手动输入' : '未提供来源地址')}
                        </p>
                        <p className={styles.muted}>
                            被替换时间（北京时间）：{formatTime(item.superseded_at)}
                        </p>
                        {/* 使用当前文档 ID 和这条记录的版本号生成详情地址。 */}
<Link
    className={styles.detailLink}
    to={`/knowledge/documents/${encodeURIComponent(documentId)}/versions/${item.version}`}
>
    查看 v{item.version} 正文
</Link>
                    </li>
                ))}
            </ul>
        )
    }

    return (
        <section className={styles.panel} aria-labelledby="document-versions-heading">
            <header className={styles.header}>
                <div>
                    <h2 id="document-versions-heading">历史版本</h2>
                    <p className={styles.muted}>查看已被替换的内容版本，当前版本不包含在列表中。</p>
                </div>
                {showActions && (
                    <button type="button" className={styles.button}
                        aria-disabled={busy} onClick={refreshVersions}>
                        {busy ? '等待查询完成……' : '刷新历史版本'}
                    </button>
                )}
            </header>
            <p className={styles.status} role="status" aria-live="polite" aria-atomic="true">
                {canManage && hasValidDocumentId ? statusMessage : ''}
            </p>
            {renderVersions()}
            {/* 翻页时保留这些按钮，避免键盘焦点随加载内容消失。 */}
            {showActions && (
                <nav className={styles.pagination} aria-label="历史版本分页">
                    <span className={styles.muted}>每页最多 {PAGE_SIZE} 条</span>
                    <div className={styles.actions}>
                        <button type="button" className={styles.button}
                            aria-disabled={busy || offset === 0}
                            onClick={() => changePage(Math.max(0, offset - PAGE_SIZE))}>
                            上一页
                        </button>
                        <button type="button" className={styles.button}
                            aria-disabled={busy || !hasNext}
                            onClick={() => changePage(offset + PAGE_SIZE)}>
                            下一页
                        </button>
                        {offset > 0 && (
                            <button type="button" className={styles.button}
                                aria-disabled={busy} onClick={() => changePage(0)}>
                                返回第一页
                            </button>
                        )}
                    </div>
                </nav>
            )}
        </section>
    )
}
