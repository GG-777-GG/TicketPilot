/**
 * 文件职责：提供工单状态选择、保存和重新读取入口。
 * 作用与配合：接收详情页的数据和操作函数，保存本地选择。
 * 使用位置：由 TicketPage 稳定挂载。
 * 边界：不直接调用 API；读取成功前不解除失败后的提交限制。
 */
import { useRef, useState } from 'react'

import type { TicketStatus } from '../types'
import { STATUS_OPTIONS as OPTIONS } from '../display'
import styles from './TicketEditor.module.css'

type TicketStatusEditorProps = {
    /** 最近成功读取的当前状态。 */
    currentStatus: TicketStatus | undefined

    /** 页面是否允许编辑。 */
    canEdit: boolean

    /** 页面是否允许重新读取。 */
    canReload: boolean

    /** 正在保存、查询或等待网络。 */
    busy: boolean

    /** 父页面执行保存，并返回是否确认成功。 */
    onSave: (status: TicketStatus) => Promise<boolean>

    /** 父页面重新查询，并返回是否读取成功。 */
    onReload: () => Promise<boolean>
}

/** 展示表单；保存和重新读取均由父页面执行。 */
export function TicketStatusEditor({
    currentStatus,
    canEdit,
    canReload,
    busy,
    onSave,
    onReload,
}: TicketStatusEditorProps) {
    // null 表示跟随已读取状态；用户选择后单独保存草稿。
    const [draft, setDraft] = useState<TicketStatus | null>(null)
    const [needsReview, setNeedsReview] = useState(false)
    const lockRef = useRef(false)
    const selected = draft ?? currentStatus ?? ''

    const canSave =
        canEdit && !busy && !needsReview &&
        selected !== '' && selected !== 'closed' && selected !== currentStatus

    /** 保存选择；失败后要求先重新读取，避免盲目重试。 */
    async function save(): Promise<void> {
        if (lockRef.current || !canSave) return
        lockRef.current = true

        try {
            const saved = await onSave(selected)
            if (saved) setDraft(null)
            setNeedsReview(!saved)
        } catch {
            setNeedsReview(true)
        } finally {
            lockRef.current = false
        }
    }

    /** 用户明确重新读取时，成功后用当前状态替换草稿。 */
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
            aria-labelledby="ticket-status-heading"
        >
            <h2 id="ticket-status-heading">修改工单状态</h2>
            <p className={styles.hint}>
                选择目标状态后点击保存。关闭工单需要提交审批申请，并在获批后执行。
                重新读取会用当前状态替换你的选择。
            </p>

            <label htmlFor="ticket-status-select">目标状态</label>
            <select
                id="ticket-status-select"
                className={styles.control}
                value={selected}
                disabled={!canEdit || busy || needsReview}
                onChange={(event) => {
                    if (
                        lockRef.current ||
                        !canEdit ||
                        busy ||
                        needsReview
                    ) return

                    const option = OPTIONS.find(
                        (item) => item.value === event.target.value,
                    )
                    if (option && option.value !== 'closed') setDraft(option.value)
                }}
            >
                <option value="" disabled>请先读取工单状态</option>

                {OPTIONS.map((option) => (
                    <option
                        key={option.value}
                        value={option.value}
                        disabled={option.value === 'closed'}
                    >
                        {option.label}{option.value === 'closed' ? '（需审批）' : ''}
                    </option>
                ))}
            </select>

            <div className={styles.actions}>
                <button
                    type="button"
                    className={styles.button}
                    aria-disabled={!canSave}
                    onClick={() => { void save() }}
                >
                    保存状态
                </button>
                <button
                    type="button"
                    className={styles.button}
                    aria-disabled={busy || !canReload}
                    onClick={() => { void reload() }}
                >
                    重新读取状态
                </button>
            </div>

            {needsReview && (
                <p role="alert" className={styles.hint}>
                    请先重新读取状态，读取成功后再提交。
                </p>
            )}
        </section>
    )
}
