/**
 * 文件职责：提供创建工单表单，展示校验、等待和创建结果。
 * 作用与配合：调用 useCreateTicket，成功后提供新工单详情入口。
 * 使用位置：工作台的 /tickets/new 路由。
 * 边界：不直接发送 HTTP 请求，不自动重试或跳转，不自动分配处理人。
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { useAuth } from '../../auth/hooks/useAuth'
import { useCreateTicket } from '../hooks/useCreateTicket'
import type { TicketCreateRequest } from '../types'
import { CHANNEL_OPTIONS as CHANNELS, PRIORITY_OPTIONS as PRIORITIES } from '../display'
import styles from './TicketCreatePage.module.css'

// 只有这四个字段通过文本输入修改。
type TextField =
    | 'customer_name'
    | 'contact_email'
    | 'subject'
    | 'description'

/** 按登录账号和角色隔离表单，切换身份时重建草稿。 */
export function TicketCreatePage() {
    const { session, status } = useAuth()

    if (status !== 'authenticated' || !session) {
        return <p role="alert">请先登录再创建工单。</p>
    }

    return (
        <TicketCreateForm
            key={JSON.stringify([session.user.id, session.user.role])}
        />
    )
}

/** 管理六个输入字段，并将提交交给创建 Hook。 */
function TicketCreateForm() {
    const { signOut } = useAuth()

    const {
        canCreate,
        isCreating,
        createMessage,
        createHttpStatus,
        createdTicket,
        needsCheck,
        submitTicket,
    } = useCreateTicket()

    const [form, setForm] = useState<TicketCreateRequest>({
        customer_name: '',
        contact_email: '',
        channel: 'web',
        subject: '',
        description: '',
        priority: 'medium',
    })

    const [formError, setFormError] = useState('')
    const titleRef = useRef<HTMLHeadingElement | null>(null)
    const errorRef = useRef<HTMLParagraphElement | null>(null)
    const resultRef = useRef<HTMLParagraphElement | null>(null)
    const lockRef = useRef(false)

    useEffect(() => {
        const previousTitle = document.title
        document.title = '新建工单 · TicketPilot'
        titleRef.current?.focus()

        return () => {
            document.title = previousTitle
        }
    }, [])

    useEffect(() => {
        if (formError) errorRef.current?.focus()
    }, [formError])

    useEffect(() => {
        if (createMessage && !isCreating) {
            resultRef.current?.focus()
        }
    }, [createMessage, isCreating])

    /** field 仅允许四个文本字段；value 是输入框当前内容。 */
    function changeText(field: TextField, value: string): void {
        if (!canCreate || lockRef.current) return

        setForm((previous) => ({
            ...previous,
            [field]: value,
        }))
        setFormError('')
    }

    /** 检查文本长度后提交；请求失败的说明由 Hook 提供。 */
    async function submit(): Promise<void> {
        if (!canCreate || lockRef.current) return
        setFormError('')

        const checks = [
            { value: form.customer_name, label: '客户姓名', max: 100 },
            { value: form.subject, label: '工单标题', max: 200 },
            { value: form.description, label: '问题描述', max: 4000 },
        ]

        for (const item of checks) {
            const length = Array.from(item.value.trim()).length

            if (length < 1 || length > item.max) {
                setFormError(
                    item.label +
                    '须为 1～' +
                    item.max +
                    ' 个字符，不计首尾空白。',
                )

                // 已有相同错误时也重新聚焦。
                // 首次错误由 effect 在渲染后聚焦。
                errorRef.current?.focus()
                return
            }
        }

        lockRef.current = true

        try {
            await submitTicket({ ...form })
        } finally {
            lockRef.current = false
        }
    }

    return (
        <section>
            <header className={styles.header}>
                <h1 ref={titleRef} tabIndex={-1}>新建工单</h1>
                <Link to="/tickets" className={styles.link}>
                    返回工单列表
                </Link>
            </header>

            <div className={styles.panel}>
                <p className={styles.hint}>
                    所有字段均需填写。离开页面不会撤销已提交的创建请求。
                </p>

                <form
                    onSubmit={(event) => {
                        event.preventDefault()
                        void submit()
                    }}
                >
                    <fieldset
                        className={styles.fields}
                        disabled={!canCreate}
                    >
                        <legend>客户与问题信息</legend>

                        <label className={styles.field}>
                            客户姓名
                            <input
                                type="text"
                                required
                                value={form.customer_name}
                                onChange={(event) =>
                                    changeText(
                                        'customer_name',
                                        event.target.value,
                                    )
                                }
                            />
                        </label>

                        <label className={styles.field}>
                            联系邮箱
                            <input
                                type="email"
                                required
                                value={form.contact_email}
                                onChange={(event) =>
                                    changeText(
                                        'contact_email',
                                        event.target.value,
                                    )
                                }
                            />
                        </label>

                        <label className={styles.field}>
                            提交渠道
                            <select
                                value={form.channel}
                                onChange={(event) => {
                                    if (!canCreate || lockRef.current) return

                                    const option = CHANNELS.find(
                                        (item) =>
                                            item.value === event.target.value,
                                    )

                                    if (option) {
                                        setForm((previous) => ({
                                            ...previous,
                                            channel: option.value,
                                        }))
                                    }

                                    setFormError('')
                                }}
                            >
                                {CHANNELS.map((item) => (
                                    <option
                                        key={item.value}
                                        value={item.value}
                                    >
                                        {item.label}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <label className={styles.field}>
                            优先级
                            <select
                                value={form.priority}
                                onChange={(event) => {
                                    if (!canCreate || lockRef.current) return

                                    const option = PRIORITIES.find(
                                        (item) =>
                                            item.value === event.target.value,
                                    )

                                    if (option) {
                                        setForm((previous) => ({
                                            ...previous,
                                            priority: option.value,
                                        }))
                                    }

                                    setFormError('')
                                }}
                            >
                                {PRIORITIES.map((item) => (
                                    <option
                                        key={item.value}
                                        value={item.value}
                                    >
                                        {item.label}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <label className={styles.fullField}>
                            工单标题
                            <input
                                type="text"
                                required
                                value={form.subject}
                                onChange={(event) =>
                                    changeText('subject', event.target.value)
                                }
                            />
                        </label>

                        <label className={styles.fullField}>
                            问题描述
                            <textarea
                                rows={6}
                                required
                                value={form.description}
                                onChange={(event) =>
                                    changeText(
                                        'description',
                                        event.target.value,
                                    )
                                }
                            />
                        </label>

                        <button
                            type="submit"
                            className={styles.button}
                        >
                            {createdTicket
                                ? '已创建'
                                : isCreating
                                    ? '正在创建……'
                                    : '创建工单'}
                        </button>
                    </fieldset>
                </form>

                <p
                    ref={errorRef}
                    tabIndex={-1}
                    role="alert"
                    className={styles.error}
                >
                    {formError}
                </p>

                {isCreating && (
                    <p role="status">
                        {createdTicket
                            ? '工单已创建，正在刷新列表……'
                            : '正在创建，请勿重复提交……'}
                    </p>
                )}

                <p
                    ref={resultRef}
                    tabIndex={-1}
                    role="status"
                    className={styles.result}
                >
                    {createMessage}
                </p>

                {createHttpStatus === 401 && (
                    <button
                        type="button"
                        className={styles.button}
                        onClick={signOut}
                    >
                        重新登录
                    </button>
                )}

                {createdTicket && (
                    <Link
                        className={styles.link}
                        to={
                            '/tickets/' +
                            encodeURIComponent(createdTicket.id)
                        }
                    >
                        查看新工单详情
                    </Link>
                )}

                {needsCheck && (
                    <Link className={styles.link} to="/tickets">
                        去工单列表核对
                    </Link>
                )}
            </div>
        </section>
    )
}