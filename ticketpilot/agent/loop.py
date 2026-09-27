# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# dataclass 用来创建只负责保存数据的结果对象。
from dataclasses import dataclass

# 导入工具分发器模块
from ticketpilot.agent import dispatcher as agent_dispatcher

# 导入所有模型必须遵守的接口
from ticketpilot.agent.model import AgentModel

# 知识检索工具需要使用统一的向量模型接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 导入消息和模型决定类型
from ticketpilot.agent.schemas import (
    AgentMessage,
    AgentSource,
    FinalAnswerDecision,
    SearchKnowledgeToolResult,
    ToolCallDecision,
)


# 表示 Agent 调用工具的次数超过安全限制
class AgentLoopLimitError(RuntimeError):
    # 当前不需要添加额外属性或方法
    pass

# 保存 Agent Loop 最终产生的答案和真实知识来源。
@dataclass(
    frozen=True,
    slots=True,
)
class AgentRunResult:
    # 模型生成的最终自然语言答案。
    answer: str

    # Python 从真实工具结果中提取的知识来源。
    # 没有使用知识库时，它是空元组。
    sources: tuple[
        AgentSource,
        ...,
    ]


# 运行一次完整的 Agent 对话循环
def run_agent(
    # 当前 Agent 请求使用的数据库 Session。
    session: Session,

    # 负责生成决定的对话模型。
    model: AgentModel,

    # 用户本次提出的问题。
    user_message: str,

    # 星号后的参数必须写出参数名称。
    *,

    # 知识检索工具使用的向量模型。
    embedding_provider: EmbeddingProvider,

    # 单次 Agent 最多允许执行三个工具。
    max_tool_calls: int = 3,
) -> AgentRunResult:
    # 工具调用次数至少必须允许一次
    if max_tool_calls < 1:
        # 拒绝无效的循环配置
        raise ValueError(
            "max_tool_calls 必须至少为 1",
        )

    # 创建本次 Agent 运行期间共享的工具依赖。
    tool_context = (
        agent_dispatcher.AgentToolContext(
            session=session,
            embedding_provider=(
                embedding_provider
            ),
        )
    )

    # 使用用户输入创建初始消息历史
    messages: list[AgentMessage] = [
        AgentMessage(
            role="user",
            content=user_message,
        ),
    ]

    # 记录已经真正执行了多少次工具。
    tool_call_count = 0

    # 保存本次对话真正检索到的知识来源。
    knowledge_sources: list[
        AgentSource
    ] = []

    # 保存已经记录过的知识片段 ID，
    # 避免重复调用工具时返回重复来源。
    seen_chunk_ids: set[str] = set()

    # 持续运行，直到模型给出最终回答或者发生错误
    while True:
        # 把当前消息历史交给模型
        decision = model.decide(
            # 传入列表副本，防止模型修改原列表
            messages.copy(),
        )

        # 模型决定生成最终回答时，连同真实来源一起返回。
        if isinstance(
                decision,
                FinalAnswerDecision,
        ):
            return AgentRunResult(
                answer=decision.answer,
                sources=tuple(
                    knowledge_sources
                ),
            )

        # 防御模型实现返回不支持的数据类型
        if not isinstance(
            decision,
            ToolCallDecision,
        ):
            # 这表示模型没有遵守 AgentModel 接口
            raise TypeError(
                "模型返回了不支持的决定类型",
            )

        # 执行新工具前检查次数是否已经到达上限
        if tool_call_count >= max_tool_calls:
            # 在危险工具真正执行前终止循环
            raise AgentLoopLimitError(
                "Agent 工具调用次数超过限制",
            )

        # 通过 Dispatcher 验证并执行工具
        # 把本次运行共享的工具上下文交给 Dispatcher。
        tool_result = agent_dispatcher.dispatch_tool(
            tool_context,
            decision.tool_request,
        )

        # 只有知识库工具的结果才能成为知识来源。
        if isinstance(
            tool_result,
            SearchKnowledgeToolResult,
        ):
            # 一次检索可能返回多个知识片段。
            for match in tool_result.matches:
                # 已经记录过的片段不重复添加。
                if (
                    match.chunk_id
                    in seen_chunk_ids
                ):
                    continue

                # 来源数据来自真实工具结果，
                # 而不是让模型自己生成。
                knowledge_sources.append(
                    AgentSource(
                        chunk_id=match.chunk_id,
                        document_id=(
                            match.document_id
                        ),
                        document_title=(
                            match.document_title
                        ),
                        document_version=(
                            match.document_version
                        ),
                        source_type=(
                            match.source_type
                        ),
                        source_uri=(
                            match.source_uri
                        ),
                        chunk_index=(
                            match.chunk_index
                        ),
                        similarity=(
                            match.similarity
                        ),
                    )
                )

                # 标记当前片段已经被记录。
                seen_chunk_ids.add(
                    match.chunk_id
                )

        # 把模型刚才的工具请求加入消息历史
        messages.append(
            AgentMessage(
                role="assistant",

                # 将 Pydantic 对象转换成 JSON 字符串
                content=decision.model_dump_json(),
            ),
        )

        # 把 Python 工具的执行结果加入消息历史
        messages.append(
            AgentMessage(
                role="tool",

                # 将工具结果转换成 JSON 字符串
                content=tool_result.model_dump_json(),
            ),
        )

        # 工具成功执行后增加计数
        tool_call_count += 1
