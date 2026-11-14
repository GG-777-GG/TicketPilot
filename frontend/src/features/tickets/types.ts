/**
 *  文件职责：定义工单查询、创建使用的数据类型。
 * 作用与配合：对应后端的工单响应及相关枚举。
 * 使用位置：供工单 API、后续查询 Hook 和页面使用。
 * 边界：只描述数据结构，不发送请求，也不修改工单。
 */

/** 客户提交工单的渠道。 */
export type TicketChannel = 'web' | 'phone' | 'email' | 'chat'

/** 工单优先级：低、中、高。 */
export type TicketPriority = 'low' | 'medium' | 'high'

/**
 * 工单状态：
 * open：待处理。
 * in_progress：处理中。
 * resolved：已解决。
 * closed：已关闭。
 */
export type TicketStatus =
    | 'open'
    | 'in_progress'
    | 'resolved'
    | 'closed'

/** 后端返回的一张完整工单。 */
export type TicketResponse = {
    /** 工单 ID，按字符串使用。 */
    id: string

    /** 客户姓名。 */
    customer_name: string

    /** 客户联系邮箱。 */
    contact_email: string

    /** 提交渠道。 */
    channel: TicketChannel

    /** 工单标题。 */
    subject: string

    /** 问题描述。 */
    description: string

    /** 优先级。 */
    priority: TicketPriority

    /** 当前处理状态。 */
    status: TicketStatus

    /** 当前处理人的 ID；尚未分配时为 null。 */
    assigned_to: string | null

    /** 创建时间，接口返回日期时间字符串。 */
    created_at: string

    /** 最后更新时间。 */
    updated_at: string
}

/** 列表接口直接返回当前页数组，没有 items 或 total 包装。 */
export type TicketListResponse = TicketResponse[]


/** 创建工单时提交的数据；ID、状态和时间由后端生成。 */
export type TicketCreateRequest = {
    /** 客户姓名：去除首尾空白后 1～100 个字符。 */
    customer_name: string

    /** 客户邮箱：由后端校验邮箱格式。 */
    contact_email: string

    /** 提交渠道。 */
    channel: TicketChannel

    /** 工单标题：去除首尾空白后 1～200 个字符。 */
    subject: string

    /** 问题描述：去除首尾空白后 1～4000 个字符。 */
    description: string

    /** 前端明确提交优先级，表单后续默认选择 medium。 */
    priority: TicketPriority
}