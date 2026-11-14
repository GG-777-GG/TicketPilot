/**
 * 文件职责：定义单次 Agent 问答的请求、回答与来源类型。
 * 作用与配合：对应后端 AgentChatRequest、AgentChatResponse 和 AgentSource。
 * 使用位置：供问答 API、后续操作 Hook 和页面使用。
 * 边界：只描述数据结构，不验证实际 JSON，不维护多轮会话。
 */

/** auto 由 Agent 决定工具；knowledge 要求后端先检索知识库。 */
export type AgentChatMode = 'auto' | 'knowledge'

/** 本次提交的问题；省略 mode 时默认使用 auto。 */
export type AgentChatRequest = {
    // 用户本次提出的问题。
    message: string

    // 问答模式，可以省略。
    mode?: AgentChatMode
}

/** 后端返回的一条知识来源，不包含完整正文或独立引句。 */
export type AgentSource = {
    // 本次引用的知识片段 ID。
    chunk_id: string

    // 片段所属文档的 ID。
    document_id: string

    // 来源文档标题。
    document_title: string

    // 本次引用的文档版本。
    document_version: number

    // 来源类型：手动输入、文件、网页。
    source_type: 'manual' | 'file' | 'url'

    // 文件名或来源地址；手动输入时可以为空。
    source_uri: string | null

    // 片段在文档中的顺序，从 0 开始。
    chunk_index: number

    // 对应的原始 PDF 页码；没有页码时为空数组。
    page_numbers: number[]

    // 检索相似度，范围为 0～1。
    similarity: number
}

/** 一次问答的完整结果，来源允许为空数组。 */
export type AgentChatResponse = {
    // 后端最终生成的回答。
    answer: string

    // 回答关联的知识来源。
    sources: AgentSource[]
}