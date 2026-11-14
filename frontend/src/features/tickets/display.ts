/**
 * 文件职责：集中定义工单中文文案、下拉选项和时间显示方法。
 * 作用与配合：列表、详情、创建表单和状态编辑器共用同一份定义。
 * 使用位置：工单页面与展示组件。
 * 边界：只处理展示，不决定权限、状态流转或发送请求。
 */
import type { TicketChannel, TicketPriority, TicketStatus } from './types'

// Record 要求每个枚举值都有对应文案，漏写时类型检查会报错。
export const STATUS_LABELS: Record<TicketStatus, string> = {
    open: '待处理',
    in_progress: '处理中',
    resolved: '已解决',
    closed: '已关闭',
}

export const PRIORITY_LABELS: Record<TicketPriority, string> = {
    low: '低',
    medium: '中',
    high: '高',
}

export const CHANNEL_LABELS: Record<TicketChannel, string> = {
    web: '网页',
    phone: '电话',
    email: '邮件',
    chat: '在线聊天',
}

// 显式排列选项顺序，显示文字统一从上面的字典取得。
const statusOrder: TicketStatus[] = ['open', 'in_progress', 'resolved', 'closed']
const priorityOrder: TicketPriority[] = ['low', 'medium', 'high']
const channelOrder: TicketChannel[] = ['web', 'phone', 'email', 'chat']

export const STATUS_OPTIONS = statusOrder.map((value) => ({
    value, label: STATUS_LABELS[value],
}))
export const PRIORITY_OPTIONS = priorityOrder.map((value) => ({
    value, label: PRIORITY_LABELS[value],
}))
export const CHANNEL_OPTIONS = channelOrder.map((value) => ({
    value, label: CHANNEL_LABELS[value],
}))

/** value 为接口返回的时间字符串，沿用现有北京时间显示方式。 */
export function formatTicketTime(value: string): string {
    return new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
    })
}
