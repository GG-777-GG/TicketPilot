/**
 * 文件职责：提供历史内容恢复的准备、确认和结果区域。
 * 作用与配合：读取当前文档，调用恢复 Hook，展示版本快照与结果。
 * 使用位置：由历史版本详情页在正文加载成功后显示。
 * 边界：不修改历史快照，不自动提交；实际权限与并发检查由后端执行。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { getKnowledgeDocument } from '../api'
import { useRestoreKnowledgeDocumentVersion } from '../hooks/useRestoreKnowledgeDocumentVersion'
import styles from './KnowledgeDocumentVersionRestorePanel.module.css'

type RestorePanelProps = {
    documentId: string
    version: number
    historicalTitle: string
}

/** 用不同阶段表达界面，确认时保存本次读到的当前版本。 */
type Stage =
    | { kind: 'idle'; message: string }
    | { kind: 'preparing' }
    | {
        kind: 'confirm'
        expectedVersion: number
        currentTitle: string
    }
    | { kind: 'submitting' }
    | { kind: 'result'; newVersion: number | null }

/** 先查询、再确认；只有确认按钮会触发恢复写请求。 */
export function KnowledgeDocumentVersionRestorePanel({
    documentId,
    version,
    historicalTitle,
}: RestorePanelProps) {
    const { session, status: authStatus } = useAuth()

    const { restoreVersion, isRestoring, restoreMessage } =
        useRestoreKnowledgeDocumentVersion(documentId, version)

    const [stage, setStage] = useState<Stage>({
        kind: 'idle',
        message: '',
    })

    // 为整轮界面操作立即上锁，同时用于取消准备阶段的读取。
    const actionRef = useRef<AbortController | null>(null)
    const headingRef = useRef<HTMLHeadingElement | null>(null)

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // key 负责重置确认状态；这里取消旧读取，使旧回调失效。
    useLayoutEffect(() => {
        return () => {
            const controller = actionRef.current
            actionRef.current = null
            controller?.abort()
        }
    }, [
        documentId,
        version,
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])

    // 操作按钮消失或切换后，将焦点移到仍存在的区域标题。
    useEffect(() => {
        if (stage.kind !== 'idle') {
            headingRef.current?.focus()
        }
    }, [stage.kind])

    /** 查询当前文档，保存用于确认的版本号和标题。 */
    async function prepareRestore(): Promise<void> {
        if (
            actionRef.current ||
            isRestoring ||
            !session ||
            !canManage
        ) return

        const controller = new AbortController()
        actionRef.current = controller
        setStage({ kind: 'preparing' })

        try {
            const current = await getKnowledgeDocument(
                session.accessToken,
                documentId,
                controller.signal,
            )

            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            if (
                current.id !== documentId ||
                !Number.isSafeInteger(current.version) ||
                current.version < 1 ||
                !Number.isSafeInteger(version) ||
                version < 1 ||
                version >= current.version
            ) {
                setStage({
                    kind: 'idle',
                    message: '版本信息不一致，请打开当前文档核对。',
                })
                return
            }

            // 后端规定只有 ready 文档可以更新内容。
            if (current.status !== 'ready') {
                setStage({
                    kind: 'idle',
                    message: '当前文档不是 ready 状态，请先打开当前文档处理。',
                })
                return
            }

            // 保存本次确认依据，不随后续后台数据自动变化。
            setStage({
                kind: 'confirm',
                expectedVersion: current.version,
                currentTitle: current.title,
            })
        } catch (error) {
            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            setStage({
                kind: 'idle',
                message: error instanceof ApiError
                    ? '无法读取当前文档：' + error.message
                    : '暂时无法读取当前文档，请检查网络后重新准备。',
            })
        } finally {
            if (actionRef.current === controller) {
                actionRef.current = null
            }
        }
    }

    /** 仅使用用户本次确认的当前版本号提交，不自动追随后台更新。 */
    async function confirmRestore(): Promise<void> {
        if (
            stage.kind !== 'confirm' ||
            actionRef.current ||
            isRestoring ||
            !canManage
        ) return

        const expectedVersion = stage.expectedVersion
        const controller = new AbortController()

        actionRef.current = controller
        setStage({ kind: 'submitting' })

        try {
            // 写请求的取消与缓存刷新，由上一批的 Hook 负责。
            const result = await restoreVersion(expectedVersion)

            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            setStage({
                kind: 'result',
                newVersion: result?.version ?? null,
            })
        } catch {
            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            setStage({
                kind: 'idle',
                message: '暂时无法确认恢复结果，请先打开当前文档核对版本和内容。',
            })
        } finally {
            if (actionRef.current === controller) {
                actionRef.current = null
            }
        }
    }

    if (!canManage) return null

    const busy =
        stage.kind === 'preparing' ||
        stage.kind === 'submitting' ||
        isRestoring

    const showPrepare =
        stage.kind === 'idle' ||
        (stage.kind === 'result' && stage.newVersion === null)

    return (
        <section
            className={styles.panel}
            aria-labelledby="restore-version-heading"
        >
            <h2
                id="restore-version-heading"
                ref={headingRef}
                tabIndex={-1}
            >
                恢复历史内容
            </h2>

            <p className={styles.muted}>
                恢复会生成新的当前版本，并保留恢复前内容的历史记录。
            </p>

            {stage.kind === 'preparing' && (
                <p role="status">正在读取当前文档版本……</p>
            )}

            {stage.kind === 'submitting' && (
                <p role="status">正在恢复历史内容，请稍候……</p>
            )}

            {stage.kind === 'idle' && stage.message && (
                <p role="alert">{stage.message}</p>
            )}

            {stage.kind === 'confirm' && (
                <div className={styles.confirmation}>
                    <p>
                        当前文档：v{stage.expectedVersion}
                        {' · '}{stage.currentTitle}
                    </p>

                    <p>
                        恢复来源：v{version}
                        {' · '}{historicalTitle}
                    </p>

                    <p>确认将这份历史内容恢复为新的当前版本？</p>

                    <div className={styles.actions}>
                        <button
                            type="button"
                            className={styles.button}
                            disabled={busy}
                            onClick={() => {
                                setStage({ kind: 'idle', message: '' })
                                headingRef.current?.focus()
                            }}
                        >
                            取消
                        </button>

                        <button
                            type="button"
                            className={styles.primary}
                            disabled={busy}
                            onClick={() => {
                                void confirmRestore()
                            }}
                        >
                            确认恢复
                        </button>
                    </div>
                </div>
            )}

            {stage.kind === 'result' && (
                <p role={stage.newVersion === null ? 'alert' : 'status'}>
                    {restoreMessage ||
                        '暂时无法确认恢复结果，请打开当前文档核对。'}
                </p>
            )}

            <div className={styles.actions}>
                {showPrepare && (
                    <button
                        type="button"
                        className={styles.button}
                        disabled={busy}
                        onClick={() => {
                            void prepareRestore()
                        }}
                    >
                        {stage.kind === 'result'
                            ? '重新读取当前版本'
                            : '准备恢复此版本'}
                    </button>
                )}

                <Link
                    className={styles.button}
                    to={
                        '/knowledge/documents/' +
                        encodeURIComponent(documentId)
                    }
                >
                    查看当前文档
                </Link>
            </div>
        </section>
    )
}
