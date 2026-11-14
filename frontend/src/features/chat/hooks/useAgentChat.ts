/**
 * 文件职责：管理单次 Agent 问答的提交、回答、错误和停止等待。
 * 作用与配合：读取登录状态，调用 sendAgentMessage，向页面提供交互状态。
 * 使用位置：供后续 Agent 问答页调用。
 * 边界：不维护多轮记忆、不自动提交或重试；停止等待不代表后端停止计算。
 */
import { useLayoutEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { ApiError } from '../../../shared/api/http'
import { useAuth } from '../../auth/hooks/useAuth'
import { sendAgentMessage } from '../api'

import type {
    AgentChatMode,
    AgentChatResponse,
} from '../types'

/** 页面根据 status 选择提示、进度或回答。 */
export type AgentChatState =
    | { status: 'idle' }
    | {
        status: 'loading'
        question: string
        mode: AgentChatMode
    }
    | {
        status: 'success'
        question: string
        mode: AgentChatMode
        result: AgentChatResponse
    }
    | {
        status: 'error'
        error: string
        httpStatus: number | null
    }
    | { status: 'stopped' }

/** 点击发送时固定的问题、模式和身份凭据。 */
type AskAgentVariables = {
    accessToken: string
    message: string
    mode: AgentChatMode
    signal: AbortSignal
}

/** 将接口、超时或网络异常转换为页面可展示的说明。 */
function getChatError(error: unknown): string {
    if (error instanceof ApiError) {
        switch (error.status) {
            case 401:
                return '登录已失效，请重新登录。'

            case 403:
                return '当前账号不能进行这次问答。'

            case 422:
                return '问题或模式不符合要求，请检查后重新提交。'

            case 503:
                return 'AI 服务暂时不可用，请稍后再试。'

            default:
                return error.message
        }
    }

    if (
        error instanceof DOMException &&
        error.name === 'TimeoutError'
    ) {
        return '等待回答超时。后端可能仍在计算，请稍后再决定是否重新提交。'
    }

    return '暂时未能取得回答，请检查连接后手动重试。'
}

/** 仅调用 askAgent 时提交；每次显示最新一次问答的状态。 */
export function useAgentChat() {
    const { session, status: authStatus } = useAuth()

    const [state, setState] = useState<AgentChatState>({
        status: 'idle',
    })

    const requestRef = useRef<AbortController | null>(null)

    // 所有已登录角色都可提问，知识访问范围由后端决定。
    const canAsk =
        authStatus === 'authenticated' &&
        session !== null

    // 工作台的 key 负责状态重置；这里停止等待旧请求。
    useLayoutEffect(() => {
        return () => {
            const controller = requestRef.current
            requestRef.current = null
            controller?.abort()
        }
    }, [
        authStatus,
        session?.user.id,
        session?.user.role,
        session?.accessToken,
    ])

    const mutation = useMutation({
        mutationFn: (variables: AskAgentVariables) => {
            variables.signal.throwIfAborted()

            return sendAgentMessage({
                accessToken: variables.accessToken,
                data: {
                    message: variables.message,
                    mode: variables.mode,
                },
                signal: variables.signal,
            })
        },

        retry: false,

        // 立即尝试，不离线排队等联网后补发。
        networkMode: 'always',
    })

    /** 校验本次问题，提交后等待整份回答。 */
    async function askAgent(
        // 用户输入的原始问题。
        question: string,

        // 本次明确选择的模式。
        mode: AgentChatMode,
    ): Promise<void> {
        // 先检查立即锁，防止重复调用改变正在进行的状态。
        if (requestRef.current) return

        if (!session || !canAsk) {
            setState({
                status: 'error',
                error: '请先登录后再提问。',
                httpStatus: null,
            })
            return
        }

        const normalizedQuestion = question.trim()
        const length = Array.from(normalizedQuestion).length

        if (length < 1 || length > 4000) {
            setState({
                status: 'error',
                error: '问题不能为空，且不能超过 4000 个字符。',
                httpStatus: null,
            })
            return
        }

        if (mode !== 'auto' && mode !== 'knowledge') {
            setState({
                status: 'error',
                error: '请选择有效的问答模式。',
                httpStatus: null,
            })
            return
        }

        const controller = new AbortController()
        requestRef.current = controller

        setState({
            status: 'loading',
            question: normalizedQuestion,
            mode,
        })

        try {
            const result = await mutation.mutateAsync({
                accessToken: session.accessToken,
                message: normalizedQuestion,
                mode,
                signal: controller.signal,
            })

            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return

            // 空来源也是合法结果，不据此判断请求失败。
            setState({
                status: 'success',
                question: normalizedQuestion,
                mode,
                result,
            })
        } catch (error) {
            if (
                controller.signal.aborted ||
                requestRef.current !== controller
            ) return

            setState({
                status: 'error',
                error: getChatError(error),
                httpStatus:
                    error instanceof ApiError
                        ? error.status
                        : null,
            })
        } finally {
            // 旧请求结束时，不能释放新请求的锁。
            if (requestRef.current === controller) {
                requestRef.current = null
            }
        }
    }

    /** 停止前端等待，旧回答返回后不再更新页面。 */
    function stopWaiting(): void {
        const controller = requestRef.current
        if (!controller) return

        requestRef.current = null
        controller.abort()
        setState({ status: 'stopped' })
    }

    /** 清除本页显示的问答；有请求时也停止等待。 */
    function clearChat(): void {
        const controller = requestRef.current
        requestRef.current = null
        controller?.abort()
        setState({ status: 'idle' })
    }

    return {
        state,
        canAsk,
        isAsking: state.status === 'loading',
        askAgent,
        stopWaiting,
        clearChat,
    }
}
