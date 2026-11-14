/**
 * 文件职责：提供文档标题的读取、编辑、保存和结果反馈。
 * 作用与配合：读取最新标题与版本，调用标题保存 Hook。
 * 使用位置：放在当前文档详情页的内容卡片顶部。
 * 边界：不编辑正文，不自动保存；后台查询变化不会自动覆盖草稿。
 */
import {useEffect, useLayoutEffect, useRef, useState} from 'react'

import {ApiError} from '../../../shared/api/http'
import {useAuth} from '../../auth/hooks/useAuth'
import {getKnowledgeDocument} from '../api'
import {useUpdateKnowledgeDocumentTitle} from '../hooks/useUpdateKnowledgeDocumentTitle'
import styles from './KnowledgeDocumentTitleEditor.module.css'

type TitleEditorProps = {
    // 当前文档 ID。
    documentId: string

    // 页面查询成功、文档就绪且没有其他操作时，才允许编辑。
    canEdit: boolean
}

type Draft = {
    title: string
    originalTitle: string
    expectedVersion: number
}

type Mode =
    | 'idle'
    | 'loading'
    | 'editing'
    | 'saving'
    | 'failed'
    | 'saved'

/** 显式打开编辑器后读取标题，提交时使用这次编辑的版本快照。 */
export function KnowledgeDocumentTitleEditor({
                                                 documentId,
                                                 canEdit,
                                             }: TitleEditorProps) {
    const {session, status: authStatus} = useAuth()

    const {updateTitle, isSaving, saveMessage} =
        useUpdateKnowledgeDocumentTitle(documentId)

    const [mode, setMode] = useState<Mode>('idle')
    const [draft, setDraft] = useState<Draft | null>(null)
    const [readMessage, setReadMessage] = useState('')

    const actionRef = useRef<AbortController | null>(null)
    const headingRef = useRef<HTMLHeadingElement | null>(null)
    const inputRef = useRef<HTMLInputElement | null>(null)

    const canManage =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    // 状态重置交给组件的 key。
    // 这里保留请求清理，防止旧请求回来后继续更新旧编辑器。
    useLayoutEffect(() => {
        return () => {
            const controller = actionRef.current
            actionRef.current = null
            controller?.abort()
        }
    }, [
        documentId,
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])
    // 进入编辑时聚焦输入框，其他操作结果聚焦区域标题。
    useEffect(() => {
        if (mode === 'editing') {
            inputRef.current?.focus()
        } else if (mode !== 'idle') {
            headingRef.current?.focus()
        }
    }, [mode])

    const normalizedTitle = draft?.title.trim() ?? ''
    const length = Array.from(normalizedTitle).length
    const invalid = length < 1 || length > 200

    const busy =
        mode === 'loading' ||
        mode === 'saving' ||
        isSaving

    const canSave =
        mode === 'editing' &&
        canEdit &&
        !busy &&
        !!draft &&
        !invalid &&
        normalizedTitle !== draft.originalTitle &&
        Number.isSafeInteger(draft.expectedVersion) &&
        draft.expectedVersion >= 1

    /** 用户明确选择读取时，才用服务器结果替换草稿。 */
    async function openEditor(): Promise<void> {
        if (
            actionRef.current ||
            isSaving ||
            !canEdit ||
            !session ||
            !canManage
        ) return

        const controller = new AbortController()
        actionRef.current = controller
        setMode('loading')
        setReadMessage('')

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
                current.status !== 'ready' ||
                typeof current.title !== 'string' ||
                !Number.isSafeInteger(current.version) ||
                current.version < 1
            ) {
                setReadMessage(
                    '当前文档不能编辑，请刷新后核对文档状态和版本。',
                )
                setMode('failed')
                return
            }

            setDraft({
                title: current.title,
                originalTitle: current.title,
                expectedVersion: current.version,
            })

            setMode('editing')
        } catch (error) {
            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            setReadMessage(
                error instanceof ApiError
                    ? '读取标题失败：' + error.message
                    : '暂时无法读取标题，请检查网络后重试。',
            )

            setMode('failed')
        } finally {
            if (actionRef.current === controller) {
                actionRef.current = null
            }
        }
    }

    /** 保存本次草稿；失败保留草稿，成功后结束本次编辑。 */
    async function saveTitle(): Promise<void> {
        if (
            !canSave ||
            !draft ||
            !canManage ||
            actionRef.current
        ) return

        const snapshot = draft
        const controller = new AbortController()

        actionRef.current = controller
        setReadMessage('')
        setMode('saving')

        try {
            const result = await updateTitle(
                snapshot.title,
                snapshot.expectedVersion,
            )

            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            if (result) {
                setDraft(null)
                setMode('saved')
            } else {
                setMode('failed')
            }
        } catch {
            if (
                controller.signal.aborted ||
                actionRef.current !== controller
            ) return

            setReadMessage(
                '无法确认保存结果，请先核对当前文档。草稿仍然保留。',
            )
            setMode('failed')
        } finally {
            if (actionRef.current === controller) {
                actionRef.current = null
            }
        }
    }

    /** 放弃本次草稿，不发送保存请求。 */
    function cancelEdit(): void {
        if (actionRef.current || busy) return

        setDraft(null)
        setReadMessage('')
        setMode('idle')
        headingRef.current?.focus()
    }

    if (!canManage) return null

    return (
        <section
            className={styles.panel}
            aria-labelledby="title-editor-heading"
        >
            <h2
                id="title-editor-heading"
                ref={headingRef}
                tabIndex={-1}
            >
                修改文档标题
            </h2>

            <p className={styles.hint}>
                已就绪的文档可以修改标题，保存成功后会生成新版本。
            </p>

            {mode === 'loading' && (
                <p role="status">正在读取当前标题……</p>
            )}

            {mode === 'saving' && (
                <p role="status">正在保存标题……</p>
            )}

            {(mode === 'failed' || mode === 'saved') && (
                <p
                    id="title-editor-feedback"
                    role={mode === 'failed' ? 'alert' : 'status'}
                >
                    {readMessage ||
                        saveMessage ||
                        '请核对当前文档后再操作。'}
                </p>
            )}

            {draft && (
                mode === 'editing' ||
                mode === 'saving' ||
                mode === 'failed'
            ) && (
                <form
                    onSubmit={event => {
                        event.preventDefault()
                        void saveTitle()
                    }}
                >
                    <p className={styles.hint}>
                        本次编辑基于 v{draft.expectedVersion}
                    </p>

                    <label htmlFor="document-title-input">
                        文档标题
                    </label>

                    <input
                        id="document-title-input"
                        ref={inputRef}
                        className={styles.input}
                        value={draft.title}
                        disabled={busy}
                        readOnly={mode !== 'editing' || !canEdit}
                        aria-invalid={invalid}
                        aria-describedby={
                            mode === 'failed'
                                ? 'document-title-hint title-editor-feedback'
                                : 'document-title-hint'
                        }
                        onChange={event => {
                            // 先取字符串，再交给状态更新函数。
                            const title = event.currentTarget.value

                            setDraft(previous =>
                                previous
                                    ? {...previous, title}
                                    : previous,
                            )
                        }}
                    />

                    <p id="document-title-hint" className={styles.hint}>
                        {length === 0
                            ? '标题不能为空。'
                            : length > 200
                                ? '标题不能超过 200 个字符。'
                                : '标题长度：' + length +
                                ' / 200。修改标题后才能保存。'}
                    </p>

                    <div className={styles.actions}>
                        <button
                            type="button"
                            className={styles.button}
                            disabled={busy}
                            onClick={cancelEdit}
                        >
                            取消编辑
                        </button>

                        <button
                            type="submit"
                            className={styles.primary}
                            disabled={!canSave}
                        >
                            保存标题
                        </button>
                    </div>
                </form>
            )}

            {mode === 'failed' && draft && (
                <p className={styles.hint}>
                    草稿已保留。重新读取标题会替换草稿，
                    可以先复制输入框内容。
                </p>
            )}

            {!canEdit && !busy && (
                <p className={styles.hint}>
                    当前暂不能编辑，请刷新文档并确认状态为“已就绪”。
                </p>
            )}

            {(
                mode === 'idle' ||
                mode === 'failed' ||
                mode === 'saved'
            ) && (
                <div className={styles.actions}>
                    <button
                        type="button"
                        className={styles.button}
                        disabled={!canEdit || busy}
                        onClick={() => {
                            void openEditor()
                        }}
                    >
                        {draft
                            ? '重新读取标题（替换草稿）'
                            : '修改标题'}
                    </button>
                </div>
            )}
        </section>
    )
}
