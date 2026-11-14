/**
 * 文件职责：封装工单查询、创建、修改和删除请求。
 * 作用与配合：复用公共 HTTP 工具，携带当前账号的 JWT。
 * 使用位置：供工单查询与写操作 Hook 调用。
 * 边界：不保存页面状态，不绘制操作确认界面。
 */

import {
    requestJson,
    requestNoContent,
} from '../../shared/api/http'
import type {
    TicketListResponse,
    TicketResponse,
    TicketStatus,
    TicketCreateRequest,
} from './types'


/** 查询列表时使用的令牌、分页参数和取消信号。 */
type ListTicketsOptions = {
    /** 当前登录账号的 JWT。 */
    accessToken: string

    /** 最多读取多少条；省略时使用 20。 */
    limit?: number

    /** 跳过多少条；省略时使用 0。 */
    offset?: number

    /** 可选的取消信号。 */
    signal?: AbortSignal
}

/** 分页读取工单，返回当前页的工单数组。 */
export function listTickets(
    options: ListTicketsOptions,
): Promise<TicketListResponse> {
    // 把分页参数转换为 URL 查询字符串。
    const query = new URLSearchParams({
        limit: String(options.limit ?? 20),
        offset: String(options.offset ?? 0),
    })

    return requestJson<TicketListResponse>(
        '/v1/tickets?' + query.toString(),
        {
            method: 'GET',
            headers: {
                Authorization: 'Bearer ' + options.accessToken,
            },
            signal: options.signal,
        },
    )
}

/** 根据工单 ID 查询详情。 */
export function getTicket(
    // 当前登录账号的 JWT。
    accessToken: string,
    // 后端返回的工单 ID，保持字符串形式。
    ticketId: string,
    // 可选的取消信号。
    signal?: AbortSignal,
): Promise<TicketResponse> {
    return requestJson<TicketResponse>(
        '/v1/tickets/' + encodeURIComponent(ticketId),
        {
            method: 'GET',
            headers: {
                Authorization: 'Bearer ' + accessToken,
            },
            signal,
        },
    )
}

/** 修改状态时使用的身份、工单和目标状态。 */
type UpdateTicketStatusOptions = {
    /** 当前登录账号的 JWT。 */
    accessToken: string

    /** 准备修改的工单 ID。 */
    ticketId: string

    /** 准备保存的新状态。 */
    status: TicketStatus

    /** 可选的取消信号；取消等待不代表撤销后端修改。 */
    signal?: AbortSignal
}

/** 修改工单状态，返回后端保存后的完整工单。 */
export function updateTicketStatus(
    options: UpdateTicketStatusOptions,
): Promise<TicketResponse> {
    return requestJson<TicketResponse>(
        '/v1/tickets/' +
            encodeURIComponent(options.ticketId) +
            '/status',
        {
            method: 'PATCH',
            headers: {
                Authorization: 'Bearer ' + options.accessToken,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                status: options.status,
            }),
            signal: options.signal,
        },
    )
}

/** 修改处理人时需要的身份、工单和目标值。 */
type UpdateTicketAssigneeOptions = {
    /** 当前登录账号的 JWT。 */
    accessToken: string

    /** 准备修改的工单 ID。 */
    ticketId: string

    /** 处理人文本；null 表示取消分配。 */
    assignedTo: string | null

    /** 停止等待的信号；不代表撤销后端写入。 */
    signal?: AbortSignal
}

/** 分配或取消分配处理人，返回更新后的完整工单。 */
export function updateTicketAssignee(
    options: UpdateTicketAssigneeOptions,
): Promise<TicketResponse> {
    return requestJson<TicketResponse>(
        '/v1/tickets/' +
            encodeURIComponent(options.ticketId) +
            '/assignee',
        {
            method: 'PATCH',
            headers: {
                Authorization: 'Bearer ' + options.accessToken,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                assigned_to: options.assignedTo,
            }),
            signal: options.signal,
        },
    )
}

/** 创建请求的登录身份、表单数据与取消配置。 */
type CreateTicketOptions = {
    /** 当前登录账号的 JWT。 */
    accessToken: string

    /** 用户填写的创建数据。 */
    data: TicketCreateRequest

    /** 停止等待的信号，不代表撤销后端创建。 */
    signal?: AbortSignal
}

/** 创建工单；成功返回后端生成的完整工单。 */
export function createTicket(
    options: CreateTicketOptions,
): Promise<TicketResponse> {
    return requestJson<TicketResponse>('/v1/tickets', {
        method: 'POST',
        headers: {
            Authorization: 'Bearer ' + options.accessToken,
            'Content-Type': 'application/json',
        },

        // 明确挑选六个创建字段，避免把响应字段一并提交。
        body: JSON.stringify({
            customer_name: options.data.customer_name,
            contact_email: options.data.contact_email,
            channel: options.data.channel,
            subject: options.data.subject,
            description: options.data.description,
            priority: options.data.priority,
        }),

        signal: options.signal,
    })
}

/** 删除指定工单；成功返回 204，没有 JSON 正文。 */
export function deleteTicket(
    // 当前登录账号的 JWT。
    accessToken: string,

    // 用户明确确认的工单 ID。
    ticketId: string,

    // 停止等待，不代表撤销后端删除。
    signal?: AbortSignal,
): Promise<void> {
    return requestNoContent(
        '/v1/tickets/' + encodeURIComponent(ticketId),
        {
            method: 'DELETE',
            headers: {
                Authorization: 'Bearer ' + accessToken,
            },
            signal,
        },
    )
}