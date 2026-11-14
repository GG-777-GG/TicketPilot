/**
 * 文件职责：定义入库任务的上传输入、任务状态和公开响应类型。
 * 作用与配合：为请求函数、查询 Hook 和任务面板提供与后端字段对应的数据约定。
 * 使用位置：入库功能的 api.ts、useIngestionJobs 和 IngestionJobsPanel 组件。
 * 边界：只提供编译期类型，不发请求、不管理缓存，也不实现上传或任务重试。
 */

/** 入库任务的四种状态，与后端任务状态枚举一致。 */
export type IngestionJobStatus =
  | 'queued'     // 等待 Worker 处理。
  | 'running'    // Worker 正在处理。
  | 'succeeded'  // 入库成功。
  | 'failed'     // 处理失败。
  | 'cancelled'  // 已取消，不再由 Worker 领取。
  | 'cancelling' // 已申请取消，等待 Worker 确认。

/** Worker 当前执行的阶段，与后端枚举保持一致。 */
export type IngestionProcessingStage =
  | 'parsing'   // 解析文件，必要时执行 OCR。
  | 'cleaning'  // 清洗提取出的文字。
  | 'chunking'  // 切分知识片段。
  | 'embedding' // 为片段生成向量。
  | 'indexing'  // 保存文档、片段和向量。

/**
 * 一条入库任务的公开响应。
 * 字段名称与后端 KnowledgeIngestionJobResponse 保持一致。
 */
export type IngestionJobResponse = {
  // 任务唯一 ID，用于查询详情和重试。
  id: string

  // 用户提交的文档标题。
  title: string

  // 文档来源类型；url 是后端枚举中预留的来源值。
  source_type: 'manual' | 'file' | 'url'

  // 来源信息，例如原始文件名；手动输入时通常为空。
  source_uri: string | null

  // 文档可见范围：内部可见或受限可见。
  visibility: 'internal' | 'restricted'

  // 提交人的用户 ID，由后端根据登录身份确定。
  requested_by: string

  // 当前任务状态。
  status: IngestionJobStatus

  // Worker 当前阶段；尚未上报或任务完成时可以为空。
  processing_stage: IngestionProcessingStage | null

  // Worker 实际领取处理的次数，新任务为 0。
  attempt_count: number

  // 成功后生成的知识文档 ID；尚未成功时可以为空。
  document_id: string | null

  // 最近一次失败原因；没有失败记录时为空。
  // 失败任务重新排队后，也可能保留上次的错误原因。
  last_error: string | null

  // 后端通过 JSON 返回时间字符串，前端收到的不是 Date 对象。
  created_at: string

  // 任务最后更新时间。
  updated_at: string
}

/** 分页查询任务列表时，后端返回的整体结构。 */
export type IngestionJobListResponse = {
  // 当前页的任务数组。
  items: IngestionJobResponse[]

  // 当前用户权限和筛选条件范围内的任务总数。
  total: number

  // 本次查询每页最多返回多少条。
  limit: number

  // 本次查询跳过了多少条任务。
  offset: number
}

/** 提交文件入库任务时，前端需要提供的业务数据。 */
export type FileIngestionJobInput = {
  // 用户通过浏览器选中的文件对象，包含文件名、大小和内容。
  file: File

  // 文档标题，后端要求去除首尾空白后为 1～200 个字符。
  title: string

  // 提交时明确指定文档可见范围。
  visibility: 'internal' | 'restricted'

}