/**
 * 文件职责：定义知识文档的状态、摘要、详情和分页响应类型。
 * 作用与配合：与后端文档响应 Schema 对应，供 API、Hook 和页面使用。
 * 使用位置：知识文档列表，以及后续文档详情和管理功能。
 * 边界：只提供编译期类型，不请求接口、不创建数据库表，
 *       也不会自动校验后端 JSON 的实际内容。
 */

/** 知识文档状态，与后端 KnowledgeDocumentStatus 对应。 */
export type KnowledgeDocumentStatus =
    | 'pending'     // 已接收，等待处理。
    | 'processing'  // 正在处理。
    | 'ready'       // 已就绪，可在权限允许时参与检索。
    | 'failed'      // 文档处理失败。
    | 'archived'    // 已归档，不参与正常检索。

/**
 * 一篇知识文档的公开摘要。
 *
 * 对应后端 KnowledgeDocumentResponse。
 * 列表只返回这些摘要字段，不携带整篇正文。
 */
export type KnowledgeDocumentResponse = {
    // 文档唯一 ID，用于查询详情、归档和版本管理。
    id: string

    // 文档当前标题。
    title: string

    // 文档来源：手动输入、文件或网页。
    source_type: 'manual' | 'file' | 'url'

    // 原始文件名或网页地址；手动输入时通常为空。
    source_uri: string | null

    // 当前文档状态。
    status: KnowledgeDocumentStatus

    // 文档可见范围。
    visibility: 'internal' | 'restricted'

    // 当前文档的知识片段数量。
    chunk_count: number

    // 生成向量时实际使用的模型名称。
    embedding_model: string

    // 实际存储的向量维度。
    embedding_dimensions: number

    // 当前文档版本号。
    version: number

    // 文档创建者的用户 ID。
    created_by: string

    // 后端通过 JSON 返回时间字符串，不是 Date 对象。
    created_at: string

    // 文档最后更新时间。
    updated_at: string
}

/** 分页查询知识文档列表时的整体响应。 */
export type KnowledgeDocumentListResponse = {
    // 当前页的文档摘要。
    items: KnowledgeDocumentResponse[]

    // 文档总数，不受当前页截取数量影响。
    total: number

    // 本次查询每页最多返回多少篇文档。
    limit: number

    // 本次查询跳过了多少篇文档。
    offset: number
}

/** 文档当前执行的处理阶段，与后端枚举一致。 */
export type KnowledgeProcessingStage =
    | 'parsing'   // 解析文件，必要时执行 OCR。
    | 'cleaning'  // 清洗提取出的文字。
    | 'chunking'  // 将正文切成知识片段。
    | 'embedding' // 为片段生成向量。
    | 'indexing'  // 保存文档、片段和向量。

/**
 * 单篇知识文档的详情响应。
 *
 * 保留 KnowledgeDocumentResponse 中的全部摘要字段，
 * 再增加正文、处理阶段和处理算法版本。
 */
export type KnowledgeDocumentDetailResponse =
    KnowledgeDocumentResponse & {
    // 清洗前的正文；文件上传时，这是解析得到的文字，
    // 不是原始 PDF 或 Word 文件的二进制内容。
    raw_content: string

    // 经过清洗后的正文，用于后续切块。
    cleaned_content: string

    // 当前处理阶段；没有进行中的阶段时可以为 null。
    // 字段必须存在，但值允许为空。
    processing_stage: KnowledgeProcessingStage | null

    // 清洗算法版本，例如 basic-v1。
    cleaner_version: string

    // 切块算法版本，例如 character-v1。
    chunker_version: string
}

/** 一条历史版本摘要，对应后端 KnowledgeDocumentVersionResponse。 */
export type KnowledgeDocumentVersionResponse = {
    // 这个历史版本的版本号。
    version: number

    // 这个版本当时的标题。
    title: string

    // 这个版本当时的来源类型。
    source_type: 'manual' | 'file' | 'url'

    // 当时的文件名或网页地址；允许为空。
    source_uri: string | null

    // 当时使用的向量模型和维度。
    embedding_model: string
    embedding_dimensions: number

    // 当时使用的清洗和切块算法版本。
    cleaner_version: string
    chunker_version: string

    // 最初创建这个内容版本的用户 ID 和时间。
    created_by: string
    created_at: string

    // 后来替换这个版本的用户 ID 和时间。
    superseded_by: string
    superseded_at: string
}

/** 历史版本列表的分页响应。 */
export type KnowledgeDocumentVersionListResponse = {
    // 当前查询的是哪篇文档。
    document_id: string

    // 文档当前正在使用的版本号。
    current_version: number

    // 本页历史版本摘要，不包含当前版本。
    items: KnowledgeDocumentVersionResponse[]

    // 历史版本总数，不是本页条数。
    total: number

    // 本次最多返回多少条，以及跳过多少条。
    limit: number
    offset: number
}

/** 历史版本的完整详情：摘要字段加上当时的两份正文。 */
export type KnowledgeDocumentVersionDetailResponse =
    KnowledgeDocumentVersionResponse & {
        // 这个历史版本当时保存的原始正文。
        raw_content: string

        // 这个历史版本当时清洗后的正文。
        cleaned_content: string
    }

    /**
 * 恢复历史版本时提交的请求体。
 *
 * 目标历史版本号放在 URL 中；
 * 请求体只提交操作前看到的当前版本号。
 */
export type KnowledgeDocumentVersionRestoreRequest = {
    // 用于后端并发检查，不是准备恢复的历史版本号。
    expected_version: number
}

/** 修改文档标题时提交的数据；不包含正文。 */
export type KnowledgeDocumentTitleUpdateRequest = {
    // 去掉首尾空白后的新标题，不能为空。
    title: string

    // 开始编辑时看到的当前文档版本号。
    expected_version: number
}