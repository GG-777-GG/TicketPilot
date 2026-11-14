/**
 * 文件职责：展示删除入口和目标工单的确认信息。
 * 作用与配合：接收详情页的权限、等待状态与删除函数。
 * 使用位置：供 TicketPage 在详情内容之外稳定挂载。
 * 边界：不直接发送请求；只有点击确认删除才调用 onConfirm。
 */
import { useEffect, useRef, useState } from 'react'

import styles from './TicketDeletePanel.module.css'

type DeleteTarget = {
    id: string
    subject: string
}

type TicketDeletePanelProps = {
    /** 最近成功读取的工单；没有有效详情时为 undefined。 */
    target: DeleteTarget | undefined

    /** 当前是否允许准备并执行删除。 */
    canDelete: boolean

    /** 查询或任一写操作正在等待。 */
    busy: boolean

    /** 父页面执行删除，参数是确认区保存的工单 ID。 */
    onConfirm: (ticketId: string) => Promise<boolean>
    /** 提交后关闭确认信息时，聚焦父页面的结果或标题。 */
    onCloseResult: () => void
}

/** 保存确认时的目标快照，并处理取消和确认操作。 */
export function TicketDeletePanel({
    target,
    canDelete,
    busy,
    onConfirm,
    onCloseResult,
}: TicketDeletePanelProps) {
    const [prepared, setPrepared] = useState<DeleteTarget | null>(null)
    const [submitted, setSubmitted] = useState(false)
    const [localError, setLocalError] = useState('')

    const lockRef = useRef(false)
    const prepareRef = useRef<HTMLButtonElement | null>(null)
    const cancelRef = useRef<HTMLButtonElement | null>(null)

    // 打开确认区时，将焦点放到“取消”。
    useEffect(() => {
        if (prepared) cancelRef.current?.focus()
    }, [prepared])

    const canPrepare =
        canDelete && !busy && !submitted && target !== undefined

    // 当前详情必须仍然对应用户看到的确认信息。
    const targetMatches =
        prepared !== null &&
        target !== undefined &&
        prepared.id === target.id &&
        prepared.subject === target.subject

    const canConfirm = canPrepare && targetMatches

    /** 点击准备时固定 ID 和标题，不随后台刷新偷偷改变目标。 */
    function prepare(): void {
        if (lockRef.current || !canPrepare || !target) return

        setLocalError('')
        setPrepared({
            id: target.id,
            subject: target.subject,
        })
    }

    /** 取消只关闭确认区，不调用删除函数。 */
    function cancel(): void {
        if (lockRef.current || busy) return

        setPrepared(null)
        setLocalError('')
        if (submitted) onCloseResult()
        else prepareRef.current?.focus()
    }

    /** 用户明确确认后，向父页面提交快照中的工单 ID。 */
    async function confirm(): Promise<void> {
        if (lockRef.current || !canConfirm || !prepared) return

        lockRef.current = true
        setSubmitted(true)
        setLocalError('')

        try {
            await onConfirm(prepared.id)
            // 删除结果由父页面的 Hook 提示区展示。
            // 此组件提交后不重新开放删除，避免重复操作。
        } catch {
            setLocalError('暂时无法确认删除结果，请返回列表核对。')
        } finally {
            lockRef.current = false
        }
    }

    return (
        <section
            className={styles.panel}
            aria-labelledby="ticket-delete-heading"
        >
            <h2 id="ticket-delete-heading">删除工单</h2>
            <p className={styles.hint}>
                删除后无法通过当前系统恢复，请先核对工单。
            </p>

            {/* 保持按钮挂载，取消后可以把焦点交还给它。 */}
            <button
                ref={prepareRef}
                type="button"
                className={styles.dangerButton}
                aria-expanded={prepared !== null}
                aria-controls="ticket-delete-confirmation"
                aria-disabled={!canPrepare || prepared !== null}
                onClick={() => {
                    if (prepared !== null) return
                    prepare()
                }}
            >
                准备删除
            </button>

            <div
                id="ticket-delete-confirmation"
                className={styles.confirmation}
                hidden={prepared === null}
            >
                {prepared && (
                    <>
                        <p>
                            即将删除：<strong>{prepared.subject}</strong>
                        </p>
                        <p className={styles.hint}>
                            工单 ID：{prepared.id}
                        </p>

                        {!targetMatches && !submitted && (
                            <p role="alert">
                                当前详情已变化或暂不可用，
                                请取消并重新读取后再确认。
                            </p>
                        )}

                        <div className={styles.actions}>
                            <button
                                ref={cancelRef}
                                type="button"
                                className={styles.button}
                                aria-disabled={busy}
                                onClick={cancel}
                            >
                                {submitted ? '关闭确认信息' : '取消'}
                            </button>

                            <button
                                type="button"
                                className={styles.dangerButton}
                                aria-disabled={!canConfirm}
                                onClick={() => { void confirm() }}
                            >
                                {submitted ? '已提交删除请求' : '确认删除'}
                            </button>
                        </div>
                    </>
                )}
            </div>

            {localError && <p role="alert">{localError}</p>}
        </section>
    )
}
