/**
 * 文件职责：提供单次提问表单、回答、知识来源及等待反馈。
 * 作用与配合：调用 useAgentChat，通过工作台 /chat 路由显示。
 * 使用位置：由应用路由加载，菜单提供入口。
 * 边界：不保存多轮会话，不渲染模型 HTML；权限由后端再次检查。
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { useAuth } from '../../auth/hooks/useAuth'
import { useAgentChat } from '../hooks/useAgentChat'
import type { AgentChatMode, AgentSource } from '../types'
import styles from './AgentChatPage.module.css'

const SOURCE_LABELS: Record<AgentSource['source_type'], string> = {
    manual: '手动录入',
    file: '文件',
    url: '网页',
}

/** 去除重复或无效页码，只展示来源片段实际涉及的页码。 */
function formatPages(pages: number[]): string {
    const valid = pages.filter(page =>
        Number.isSafeInteger(page) && page >= 1,
    )

    // 展开得到新数组，排序不会修改接口返回的原数组。
    const sorted = [...new Set(valid)].sort((a, b) => a - b)

    return sorted.length > 0
        ? '片段涉及页码：' + sorted.join('、')
        : '未提供页码'
}

type SourceListProps = {
    items: AgentSource[]
    canOpenDocuments: boolean
}

/** 显示来源元数据；管理账号可打开当前文档。 */
function SourceList({
    items,
    canOpenDocuments,
}: SourceListProps) {
    return (
        <section
            className={styles.sourceSection}
            aria-labelledby="chat-sources-heading"
        >
            <h3 id="chat-sources-heading">
                知识来源（{items.length}）
            </h3>

            {items.length === 0 ? (
                <p className={styles.hint}>
                    本次回答未附带知识来源。
                </p>
            ) : (
                <ul className={styles.sources}>
                    {items.map(source => (
                        <li
                            key={source.chunk_id}
                            className={styles.source}
                        >
                            <h4>{source.document_title}</h4>
                            <p>引用版本：v{source.document_version}</p>

                            <p className={styles.hint}>
                                来源：
                                {source.source_uri ||
                                    SOURCE_LABELS[source.source_type]}
                            </p>

                            <p className={styles.hint}>
                                {formatPages(source.page_numbers)}
                            </p>

                            <p className={styles.hint}>
                                检索相似度：
                                {source.similarity.toFixed(3)}
                            </p>

                            {canOpenDocuments && (
                                <Link
                                    className={styles.link}
                                    to={
                                        '/knowledge/documents/' +
                                        encodeURIComponent(source.document_id)
                                    }
                                >
                                    查看当前文档
                                </Link>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </section>
    )
}

/** 组合提问表单、请求状态、回答和来源。 */
export function AgentChatPage() {
    const { session, status: authStatus, signOut } = useAuth()

    const {
        state,
        canAsk,
        isAsking,
        askAgent,
        stopWaiting,
        clearChat,
    } = useAgentChat()

    const [question, setQuestion] = useState('')
    const [mode, setMode] = useState<AgentChatMode>('knowledge')

    const titleRef = useRef<HTMLHeadingElement | null>(null)
    const resultRef = useRef<HTMLHeadingElement | null>(null)
    const questionRef = useRef<HTMLTextAreaElement | null>(null)
    const stopRef = useRef<HTMLButtonElement | null>(null)

    // 清空时先记录意图，等输入框重新启用后再聚焦。
    const focusInputRef = useRef(false)

    const length = Array.from(question.trim()).length

    const expired =
        state.status === 'error' &&
        state.httpStatus === 401

    const canSubmit =
        canAsk &&
        !isAsking &&
        !expired &&
        length >= 1 &&
        length <= 4000

    const canOpenDocuments =
        authStatus === 'authenticated' &&
        (session?.user.role === 'admin' ||
            session?.user.role === 'supervisor')

    useEffect(() => {
        const previousTitle = document.title
        document.title = 'Agent 问答 · TicketPilot'
        titleRef.current?.focus()

        return () => {
            document.title = previousTitle
        }
    }, [])

    // 请求中方便停止，结束后方便阅读结果。
    useEffect(() => {
        if (focusInputRef.current) {
            focusInputRef.current = false
            questionRef.current?.focus()
        } else if (state.status === 'loading') {
            stopRef.current?.focus()
        } else if (state.status !== 'idle') {
            resultRef.current?.focus()
        }
    }, [state])

    /** 只有提交表单才提问，Hook 内还会检查参数和重复请求。 */
    function submitQuestion(): void {
        if (!canSubmit) return
        void askAgent(question, mode)
    }

    /** 清空本页输入与结果，进行中的请求停止等待。 */
    function clearPage(): void {
        focusInputRef.current = true
        clearChat()
        setQuestion('')
        setMode('knowledge')
    }

    const liveMessage =
        state.status === 'loading'
            ? '正在等待回答，请稍候。'
            : state.status === 'success'
                ? '回答已返回。'
                : state.status === 'stopped'
                    ? '已停止等待，后端可能仍在计算。'
                    : ''

    return (
        <section className={styles.page}>
            <header className={styles.header}>
                <h1 ref={titleRef} tabIndex={-1}>
                    Agent 问答
                </h1>

                <p className={styles.hint}>
                    提问并查看回答与知识来源。
                    每次提问独立作答，请写全必要信息。
                </p>
            </header>

            <div className={styles.grid}>
                <section
                    className={styles.card}
                    aria-labelledby="chat-form-heading"
                >
                    <h2 id="chat-form-heading">提出问题</h2>

                    <form
                        className={styles.form}
                        onSubmit={event => {
                            event.preventDefault()
                            submitQuestion()
                        }}
                    >
                        <label htmlFor="chat-mode">问答模式</label>

                        <select
                            id="chat-mode"
                            className={styles.control}
                            value={mode}
                            disabled={!canAsk || isAsking || expired}
                            onChange={event => {
                                const value = event.currentTarget.value

                                if (
                                    value === 'auto' ||
                                    value === 'knowledge'
                                ) {
                                    setMode(value)
                                }
                            }}
                        >
                            <option value="knowledge">知识库问答</option>
                            <option value="auto">自动模式</option>
                        </select>

                        <p className={styles.hint}>
                            {mode === 'knowledge'
                                ? '先检索知识库，证据不足时可能无法确认答案。'
                                : '由 Agent 根据问题决定是否调用工具。'}
                        </p>

                        <label htmlFor="chat-question">你的问题</label>

                        <textarea
                            id="chat-question"
                            ref={questionRef}
                            className={styles.control}
                            rows={6}
                            value={question}
                            disabled={!canAsk || isAsking || expired}
                            aria-invalid={length > 4000}
                            aria-describedby="chat-question-hint"
                            onChange={event => {
                                setQuestion(event.currentTarget.value)
                            }}
                            placeholder="请输入需要查询的完整问题"
                        />

                        <p
                            id="chat-question-hint"
                            className={styles.hint}
                        >
                            已输入 {length} / 4000 个字符，
                            首尾空白不计入。
                            {length > 4000 && '请缩短问题后再提交。'}
                        </p>

                        <div className={styles.actions}>
                            <button
                                type="submit"
                                className={styles.primary}
                                disabled={!canSubmit}
                            >
                                {isAsking ? '等待回答中……' : '发送问题'}
                            </button>

                            {isAsking && (
                                <button
                                    type="button"
                                    ref={stopRef}
                                    className={styles.button}
                                    onClick={stopWaiting}
                                >
                                    停止等待
                                </button>
                            )}

                            <button
                                type="button"
                                className={styles.button}
                                onClick={clearPage}
                            >
                                清空本页
                            </button>
                        </div>
                    </form>
                </section>

                <section
                    className={styles.card}
                    aria-labelledby="chat-result-heading"
                >
                    <h2
                        id="chat-result-heading"
                        ref={resultRef}
                        tabIndex={-1}
                    >
                        问答结果
                    </h2>

                    {/* 固定状态区只播报简短状态。 */}
                    <p
                        className={styles.status}
                        role="status"
                        aria-live="polite"
                        aria-atomic="true"
                    >
                        {liveMessage}
                    </p>

                    {state.status === 'idle' && (
                        <p className={styles.hint}>
                            输入问题并发送后，回答会显示在这里。
                        </p>
                    )}

                    {(
                        state.status === 'loading' ||
                        state.status === 'success'
                    ) && (
                        <div className={styles.question}>
                            <h3>本次问题</h3>
                            <p className={styles.text}>
                                {state.question}
                            </p>
                        </div>
                    )}

                    {state.status === 'error' && (
                        <div className={styles.error}>
                            <p role="alert">{state.error}</p>

                            {state.httpStatus === 401 && (
                                <button
                                    type="button"
                                    className={styles.button}
                                    onClick={signOut}
                                >
                                    重新登录
                                </button>
                            )}
                        </div>
                    )}

                    {state.status === 'success' && (
                        <>
                            <div className={styles.answer}>
                                <h3>回答</h3>

                                {/* 普通文本显示，React 会转义 HTML。 */}
                                <p className={styles.text}>
                                    {state.result.answer}
                                </p>
                            </div>

                            <SourceList
                                items={state.result.sources}
                                canOpenDocuments={canOpenDocuments}
                            />
                        </>
                    )}
                </section>
            </div>
        </section>
    )
}
