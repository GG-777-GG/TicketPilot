# 导入 Pydantic 的基础数据模型、模型配置和字段规则
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)
from uuid import UUID

# 导入 Literal，用来限制字段只能使用指定字符串
from typing import Literal

# 复用项目中已经定义好的工单优先级和工单状态类型
from ticketpilot.tickets.types import (
    Priority,
    TicketStatus,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
)

# 复用知识检索服务的默认参数。
from ticketpilot.knowledge.application.retrieval import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_RETRIEVAL_TOP_K,
    MAX_RETRIEVAL_TOP_K,
)


# 定义“查询单张工单工具”能够接收的参数
class GetTicketToolArguments(BaseModel):
    # 配置这个参数模型的统一验证规则
    model_config = ConfigDict(
        # 自动删除字符串两端的空格
        str_strip_whitespace=True,

        # 拒绝模型生成的未知参数
        extra="forbid",
    )

    # AI 调用工具时必须提供工单 ID
    ticket_id: str = Field(
        # 删除两端空格后，ID 至少包含一个字符
        min_length=1,

        # 项目的 UUID 字符串最多包含 36 个字符
        max_length=36,
    )


# 定义允许交给 AI 查看的一张工单数据
class TicketToolData(BaseModel):
    # 工单唯一编号
    id: str

    # 工单标题
    subject: str

    # 工单详细描述
    description: str

    # 工单优先级
    priority: Priority

    # 工单当前状态
    status: TicketStatus

    # 当前处理人；None 表示还没有分配
    assigned_to: str | None


# 定义“查询单张工单工具”的返回结果
class GetTicketToolResult(BaseModel):
    # True 表示找到了工单，False 表示没有找到
    found: bool

    # 给 AI 阅读的结果说明
    message: str

    # 找到时保存工单数据，没找到时为 None
    ticket: TicketToolData | None = None

# 定义 AI 生成的一次完整工具调用请求
class ToolRequest(BaseModel):
    # 配置工具请求的验证规则
    model_config = ConfigDict(
        # 自动删除工具名称两端的空格
        str_strip_whitespace=True,

        # 拒绝没有声明的额外字段
        extra="forbid",
    )

    # AI 想要调用的工具名称
    name: str = Field(
        # 工具名称不能为空
        min_length=1,

        # 防止 AI 生成异常长的工具名称
        max_length=100,
    )

    # AI 准备传给工具的原始参数
    arguments: dict[str, object]

# 模型引用的一条知识库原文证据。
class EvidenceQuote(BaseModel):
    model_config = ConfigDict(
        # 去掉模型输出中字符串两端多余的空格。
        str_strip_whitespace=True,

        # 只允许下面声明的两个字段，防止模型随意添加字段。
        extra="forbid",
    )

    # 这句话来自哪个知识片段。
    chunk_id: str = Field(min_length=1)

    # 从该片段正文中逐字摘录的原文。
    quote: str = Field(min_length=1)

# 定义模型“直接回答用户”的决定
class FinalAnswerDecision(BaseModel):
    # 配置最终回答的验证规则
    model_config = ConfigDict(
        # 自动删除回答两端的空格
        str_strip_whitespace=True,

        # 拒绝没有声明的额外字段
        extra="forbid",
    )

    # 固定为 final_answer，用来识别决定类型
    kind: Literal["final_answer"]

    # 模型准备直接返回给用户的回答
    answer: str = Field(
        # 回答不能为空
        min_length=1,

        # 限制单次回答的最大长度
        max_length=8000,
    )
    # 模型声明最终回答引用了哪些知识片段。
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
    )

    # 回答所依据的原文证据；普通聊天可以没有证据。
    # 列表中的每一项都包含“片段 ID + 对应原文”。
    evidence: list[EvidenceQuote] = Field(
        # 每次创建新回答时，都生成一个独立的空列表。
        default_factory=list,
    )

    # 检索到的原文是否足以直接回答用户的问题。
    # 默认 False：模型没有明确确认时，按证据不足处理。
    evidence_is_sufficient: bool = False

# 定义模型“请求调用工具”的决定
class ToolCallDecision(BaseModel):
    # 拒绝没有声明的额外字段
    model_config = ConfigDict(
        extra="forbid",
    )

    # 固定为 tool_call，用来识别决定类型
    kind: Literal["tool_call"]

    # 保存模型准备发出的完整工具请求
    tool_request: ToolRequest


# 模型一次可以返回两种决定中的任意一种
ModelDecision = (
    FinalAnswerDecision
    | ToolCallDecision
)

# 限制一条消息只能使用规定的角色
MessageRole = Literal[
    # 系统提供给模型的规则和身份说明
    "system",

    # 用户发送的问题
    "user",

    # AI 生成的回答或者工具请求
    "assistant",

    # Python 工具执行后返回的结果
    "tool",
]


# 定义 Agent 对话中的一条消息
class AgentMessage(BaseModel):
    # 配置消息的验证规则
    model_config = ConfigDict(
        # 自动删除消息两端的空格
        str_strip_whitespace=True,

        # 拒绝没有声明的额外字段
        extra="forbid",
    )

    # 表示这条消息是谁产生的
    role: MessageRole

    # 保存消息的具体内容
    content: str = Field(
        # 消息内容不能为空
        min_length=1,

        # 防止单条消息无限增大
        max_length=20_000,
    )

# 定义 Agent 对话接口接收的请求数据
class AgentChatRequest(BaseModel):
    # 设置请求数据的验证规则
    model_config = ConfigDict(
        # 自动删除用户问题两端的空格
        str_strip_whitespace=True,

        # 拒绝没有声明的额外字段
        extra="forbid",
    )

    # 保存用户本次提出的问题
    message: str = Field(
        # 删除空格后至少包含一个字符
        min_length=1,

        # 防止客户端一次提交过大的问题
        max_length=4000,
    )

    # auto：由 Agent 决定；knowledge：由后端强制先检索知识库。
    mode: Literal["auto", "knowledge"] = "auto"

# 定义 Agent 回答所引用的一条真实知识来源。
class AgentSource(BaseModel):
    # 拒绝未声明的额外字段。
    model_config = ConfigDict(
        extra="forbid",
    )

    # 实际被检索到的知识片段 ID。
    chunk_id: str

    # 知识片段所属文档的 ID。
    document_id: str

    # 给用户展示的文档标题。
    document_title: str

    # 回答引用的是文档的哪个版本。
    document_version: int = Field(
        ge=1,
    )

    # 知识来源类型，例如 manual、file 或 url。
    source_type: KnowledgeSourceType

    # 文件路径或者网页地址；手动输入时可以是 None。
    source_uri: str | None

    # 当前片段在文档中的顺序。
    chunk_index: int = Field(
        ge=0,
    )
    # 回答引用的 PDF 原始页码；非 PDF 来源使用空列表。
    page_numbers: list[int]

    # 当前知识片段与用户问题的相似度。
    similarity: float = Field(
        ge=0.0,
        le=1.0,
    )

# 定义 Agent 对话接口返回的数据
# 定义 Agent 对话接口返回的数据。
class AgentChatResponse(BaseModel):
    # 保存 Agent 最终生成的自然语言回答。
    answer: str = Field(
        min_length=1,
        max_length=8000,
    )

    # 保存回答实际使用的知识来源。
    # 普通聊天或只查询工单时，可以是空列表。
    sources: list[AgentSource] = Field(
        default_factory=list,
    )

# 定义“搜索企业知识库工具”能够接收的参数。
class SearchKnowledgeToolArguments(
    BaseModel
):
    model_config = ConfigDict(
        # 自动删除问题两端的空格。
        str_strip_whitespace=True,

        # 拒绝模型生成未知参数。
        extra="forbid",
    )

    # 模型希望从知识库中查询的问题。
    query: str = Field(
        min_length=1,
        max_length=2000,
    )

    # 最多返回多少条相关知识片段。
    top_k: int = Field(
        default=DEFAULT_RETRIEVAL_TOP_K,
        ge=1,
        le=MAX_RETRIEVAL_TOP_K,
    )

    # 过滤低于该相似度的片段。
    min_similarity: float = Field(
        default=DEFAULT_MIN_SIMILARITY,
        ge=0.0,
        le=1.0,
    )

# 定义允许交给模型阅读的一条知识匹配结果。
class KnowledgeToolMatch(
    BaseModel
):
    model_config = ConfigDict(

        # 拒绝模型生成未知参数。
        extra="forbid",
    )
    # 知识片段 ID。
    chunk_id: str

    # 片段所属文档 ID。
    document_id: str

    # 文档标题，用于模型说明答案来源。
    document_title: str

    # 当前知识文档版本。
    document_version: int = Field(
        ge=1
    )

    # manual、file 或 url。
    source_type: KnowledgeSourceType

    # 文件名或者网页地址。
    source_uri: str | None

    # 片段在文档中的顺序。
    chunk_index: int = Field(
        ge=0
    )

    # 真正交给模型阅读的知识正文。
    content: str = Field(
        min_length=1
    )

    # 片段在清洗后正文中的开始位置。
    start_offset: int = Field(
        ge=0
    )

    # 片段在清洗后正文中的结束位置。
    end_offset: int = Field(
        ge=1
    )

    # 片段对应的 PDF 原始页码；没有页码时为空列表。
    page_numbers: list[int] = Field(
        default_factory=list
    )

    # 当前片段与用户问题的相似度。
    similarity: float = Field(
        ge=0.0,
        le=1.0,
    )


# 定义知识检索工具的完整返回结果。
class SearchKnowledgeToolResult(
    BaseModel
):
    # True 表示找到了相关知识。
    found: bool

    # 给模型阅读的简短结果说明。
    message: str

    # 实际执行检索的问题。
    query: str

    # 按相关度排列的知识片段。
    matches: list[
        KnowledgeToolMatch
    ] = Field(
        default_factory=list
    )

class RequestTicketClosureArguments(BaseModel):
    """模型只能提供操作目标和申请理由。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    ticket_id: UUID
    request_reason: str = Field(min_length=1, max_length=500)


class RequestTicketClosureResult(BaseModel):
    """返回已保存的申请编号，以及是否确认启动成功。"""

    approval_id: UUID
    operation_id: UUID
    ticket_id: UUID

    workflow_started: bool
    message: str