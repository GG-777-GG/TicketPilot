/**
 * 文件职责：提供处理人输入、保存、取消分配和重新读取入口。
 * 作用与配合：接收详情页数据与操作函数，保存未提交的输入。
 * 使用位置：由 TicketPage 在详情内容之外稳定挂载。
 * 边界：不直接调用 API，不查询用户列表，不将空输入自动视为取消分配。
 */
import { useRef, useState } from 'react'

import styles from './TicketEditor.module.css'

type TicketAssigneeEditorProps = {
    /** undefined 表示尚未取得有效详情；null 表示当前未分配。 */
    currentAssignee: string | null | undefined

    /** 页面是否允许编辑。 */
    canEdit: boolean

    /** 页面是否允许重新读取。 */
    canReload: boolean

    /** 查询或任一保存操作正在等待。 */
    busy: boolean

    /** 字符串分配处理人，null 取消分配；返回是否确认成功。 */
    onSave: (assignedTo: string | null) => Promise<boolean>

    /** 重新读取完整工单，返回是否成功。 */
    onReload: () => Promise<boolean>
}

/** 编辑处理人；实际请求交给父页面提供的函数。 */
export function TicketAssigneeEditor({
    currentAssignee,
    canEdit,
    canReload,
    busy,
    onSave,
    onReload,
}: TicketAssigneeEditorProps) {
    // null 表示尚未单独输入；空字符串表示用户主动清空了输入框。
    const [draft, setDraft] = useState<string | null>(null)
    const [needsReview, setNeedsReview] = useState(false)
    const lockRef = useRef(false)

    const value = draft ?? currentAssignee ?? ''
    const normalized = value.trim()
    const length = Array.from(normalized).length

    const editable =
        canEdit && !busy && !needsReview &&
        currentAssignee !== undefined

    const canSave =
        editable && length >= 1 && length <= 100 &&
        normalized !== currentAssignee

    const canClear = editable && currentAssignee !== null

    /** 根据按钮意图保存文本或明确取消分配。 */
    async function save(assignedTo: string | null): Promise<void> {
        const allowed = assignedTo === null ? canClear : canSave
        if (lockRef.current || !allowed) return
        lockRef.current = true

        try {
            const saved = await onSave(assignedTo)
            if (saved) setDraft(null)
            setNeedsReview(!saved)
        } catch {
            setNeedsReview(true)
        } finally {
            lockRef.current = false
        }
    }

    /** 读取成功后，用后端当前处理人替换草稿。 */
    async function reload(): Promise<void> {
        if (lockRef.current || busy || !canReload) return
        lockRef.current = true

        try {
            const loaded = await onReload()
            if (loaded) setDraft(null)
            setNeedsReview(!loaded)
        } catch {
            setNeedsReview(true)
        } finally {
            lockRef.current = false
        }
    }

    return (
        <section
            className={styles.editor}
            aria-labelledby="ticket-assignee-heading"
        >
            <h2 id="ticket-assignee-heading">分配处理人</h2>

            <p id="ticket-assignee-hint" className={styles.hint}>
                输入 1～100 个字符。清空输入框不会取消分配，
                请使用“取消分配”按钮。重新读取会替换输入内容。
            </p>

            <label htmlFor="ticket-assignee-input">处理人</label>
            <input
                id="ticket-assignee-input"
                className={styles.control}
                type="text"
                value={value}
                disabled={!editable}
                aria-describedby="ticket-assignee-hint ticket-assignee-count"
                aria-invalid={length > 100}
                onChange={(event) => {
                    if (lockRef.current || !editable) return
                    setDraft(event.target.value)
                }}
            />

            <p id="ticket-assignee-count" className={styles.hint}>
                去除首尾空白后 {length}/100 个字符
            </p>

            <div className={styles.actions}>
                <button
                    type="button"
                    className={styles.button}
                    aria-disabled={!canSave}
                    onClick={() => { void save(normalized) }}
                >
                    保存处理人
                </button>

                <button
                    type="button"
                    className={styles.button}
                    aria-disabled={!canClear}
                    onClick={() => { void save(null) }}
                >
                    取消分配
                </button>

                <button
                    type="button"
                    className={styles.button}
                    aria-disabled={busy || !canReload}
                    onClick={() => { void reload() }}
                >
                    重新读取处理人
                </button>
            </div>

            {needsReview && (
                <p role="alert" className={styles.hint}>
                    请先重新读取处理人，读取成功后再操作。
                </p>
            )}
        </section>
    )
}