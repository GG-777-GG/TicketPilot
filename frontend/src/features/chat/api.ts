/**
 * 文件职责：封装单次 Agent 问答的 HTTP 请求。
 * 作用与配合：携带 JWT，复用公共请求工具，等待完整 JSON 回答。
 * 使用位置：供后续问答操作 Hook 调用。
 * 边界：不保存会话、不自动重试，也不处理流式输出。
 */
import { requestJson } from '../../shared/api/http'

import type {
    AgentChatRequest,
    AgentChatResponse,
} from './types'

/**
 * 本地模型可能多次调用工具，
 * 为问答单独设置五分钟等待预算。
 */
const AGENT_CHAT_TIMEOUT_MS = 300_000

/** 令牌、问题和取消信号分别交给请求的不同部分。 */
type SendAgentMessageOptions = {
    // 当前账号的 JWT，不包含 Bearer 前缀。
    accessToken: string

    // 本次问题和问答模式。
    data: AgentChatRequest

    // 可选信号，用于停止前端等待。
    signal?: AbortSignal
}

/** 发起一次问答，成功返回完整回答，失败继续抛给调用方。 */
export function sendAgentMessage(
    options: SendAgentMessageOptions,
): Promise<AgentChatResponse> {
    return requestJson<AgentChatResponse>(
        '/v1/agent/chat',
        {
            method: 'POST',

            headers: {
                Authorization: 'Bearer ' + options.accessToken,
                'Content-Type': 'application/json',
            },

            // 只发送后端允许的两个字段。
            body: JSON.stringify({
                message: options.data.message,
                mode: options.data.mode ?? 'auto',
            }),

            signal: options.signal,
        },

        // 第三个参数覆盖这一次请求的等待时间。
        AGENT_CHAT_TIMEOUT_MS,
    )
}