# 导入 Pydantic 的基础数据模型、模型配置和字段规则
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

# 导入 Literal，用来限制字段只能使用指定字符串
from typing import Literal

# 复用项目中已经定义好的工单优先级和工单状态类型
from ticketpilot.schemas import (
    Priority,
    TicketStatus,
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