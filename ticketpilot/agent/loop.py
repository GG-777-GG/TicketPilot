# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session
from pathlib import Path
from ticketpilot.identity.models import UserRecord
from ticketpilot.agent.tools.approvals import parse_closure_request
from ticketpilot.approvals.service import ApprovalAccessError
from ticketpilot.observability.spans import operation_span

# dataclass 用来创建只负责保存数据的结果对象。
from dataclasses import dataclass

# 导入工具分发器模块
from ticketpilot.agent import dispatcher as agent_dispatcher

# 导入所有模型必须遵守的接口
from ticketpilot.agent.model import AgentModel

from ticketpilot.agent.evidence import find_original_quote

# 知识检索工具需要使用统一的向量模型接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# Python 标准库提供的高精度计时函数。
# 用“结束读数 - 开始读数”计算耗时，单位是秒。
from time import perf_counter

# 导入消息和模型决定类型
from ticketpilot.agent.schemas import (
    AgentMessage,
    AgentSource,
    FinalAnswerDecision,
    SearchKnowledgeToolResult,
    ToolCallDecision,
    ToolRequest,
    RequestTicketClosureResult,
)

from ticketpilot.agent.verification import  EvidenceVerifier


# 表示 Agent 调用工具的次数超过安全限制
class AgentLoopLimitError(RuntimeError):
    # 当前不需要添加额外属性或方法
    pass

class InvalidAgentCitationError(RuntimeError):
    """表示模型引用了本次并未检索到的知识片段。"""

class InvalidAgentEvidenceError(RuntimeError):
    """表示模型提供的引句不是本次检索到的原文。"""

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

    # 本次 Agent 允许检索的文档类型；旧调用默认只看 internal。
    allowed_visibilities: tuple[str, ...] = ("internal",),

    # 单次 Agent 最多允许执行三个工具。
    max_tool_calls: int = 3,

    require_knowledge_search: bool = False,

    evidence_verifier: EvidenceVerifier | None = None,
    current_user: UserRecord | None = None,
    checkpoint_path: Path | None = None,
    use_mcp_tickets: bool = False,
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
            # 所有工具共用本次运行的权限范围。
            allowed_visibilities=allowed_visibilities,
            current_user=current_user,
            checkpoint_path=checkpoint_path,
            use_mcp_tickets=use_mcp_tickets,
            closure_request=(
                parse_closure_request(user_message)
                if not require_knowledge_search else None
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

    # 片段 ID → 本次工具实际返回的原文。
    knowledge_contents: dict[str, str] = {}

    knowledge_search_attempted = False

    # 持续运行，直到模型给出最终回答或者发生错误
    while True:
        # 把当前消息历史交给模型
        # knowledge 模式第一次先由后端发起检索，不让模型跳过。
        if require_knowledge_search and not knowledge_search_attempted:
            decision = ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": user_message},
                ),
            )
        else:
            # knowledge 模式已完成一次检索后，只允许模型给出最终决定。
            # 这能阻止小模型反复执行完全相同的知识检索。
            allow_tool_calls = not (
                require_knowledge_search and knowledge_search_attempted
            )
            # 记录调用模型之前的计时读数。
            model_started_at = perf_counter()

            try:
                # 保持原来的模型调用不变。
                with operation_span("model.decide"):
                    decision = model.decide(
                        # 创建消息列表的浅拷贝。
                        # 防止模型实现直接增删原来的消息列表。
                        messages.copy(),

                        # 保持当前流程对工具调用的限制。
                        allow_tool_calls=allow_tool_calls,
                    )
            finally:
                # finally：无论调用成功还是抛出异常，都执行这里。
                # 因此模型超时时，也能看到已经等待了多久。
                model_elapsed_seconds = (
                        perf_counter() - model_started_at
                )

                # 暂时输出到后端终端，方便本地学习与排查。
                # 不打印问题、原文、令牌等敏感内容。
                print(
                    f"[Agent 耗时] 模型决定："
                    f"{model_elapsed_seconds:.2f} 秒",
                    flush=True,
                )

            # 即使模型实现没有遵守约束，也不再重复执行工具。
            if not allow_tool_calls and isinstance(decision, ToolCallDecision):
                return AgentRunResult(
                    answer="根据当前检索结果，无法确认答案。",
                    sources=(),
                )

        # 模型决定生成最终回答时，连同真实来源一起返回。
        if isinstance(decision, FinalAnswerDecision):
            # 模型声明它在回答中引用的片段 ID。
            cited_ids = set(decision.cited_chunk_ids)

            # 查过知识库但证据不足时，即使模型附了真实引句也不能当作答案。
            if knowledge_search_attempted and not decision.evidence_is_sufficient:
                return AgentRunResult(
                    answer="根据当前检索结果，无法确认答案。",
                    sources=(),
                )

            # 两个集合相减：找出模型声称引用、但工具从未返回的 ID。
            unknown_ids = cited_ids - seen_chunk_ids
            if unknown_ids:
                raise InvalidAgentCitationError(
                    "Agent 引用了未检索到的知识片段"
                )

            # 核对模型提供的引句是否真的来自对应片段。
            evidence_ids: set[str] = set()
            verified_quotes: list[str] = []

            # 逐个片段、逐句核对原文证据。
            # 逐条检查模型给出的证据。
            for evidence_item in decision.evidence:
                # EvidenceQuote 的两个固定字段。
                chunk_id = evidence_item.chunk_id
                quote = evidence_item.quote

                # 证据只能对应模型声明引用的片段。
                if chunk_id not in cited_ids:
                    raise InvalidAgentEvidenceError(
                        "证据片段没有被引用"
                    )

                # 根据片段 ID，取回工具实际检索到的原文。
                content = knowledge_contents.get(chunk_id)
                if content is None:
                    raise InvalidAgentEvidenceError(
                        "证据片段不存在"
                    )

                # 只容忍句间换行差异，并取回真实原文用于后续判断。
                original_quote = (
                    find_original_quote(quote, content)
                    if len(quote) <= 2000
                    else None
                )
                if original_quote is None or len(original_quote) > 2000:
                    raise InvalidAgentEvidenceError(
                        "证据引句不在检索原文中"
                    )

                # 记录这个片段已有经过核对的证据。
                evidence_ids.add(chunk_id)
                verified_quotes.append(original_quote)

            # 每个被引用的片段都必须有原文证据。
            if evidence_ids != cited_ids:

                raise InvalidAgentEvidenceError(
                    "引用片段缺少原文证据"
                )

            if evidence_verifier is not None and cited_ids:
                # 供可选的证据充分性校验器逐条检查。
                quotes = tuple(verified_quotes)
                # 记录独立证据校验开始时的计时读数。
                verification_started_at = perf_counter()

                try:
                    # 先保存校验结果，方便计时结束后再决定是否拒答。
                    evidence_supported = evidence_verifier.verify(
                        # 用户实际提出的问题。
                        question=user_message,

                        # 已经检查过出处的原文引句。
                        evidence=quotes,
                    )
                finally:
                    # 包含校验器内部实际执行的一次或两次模型调用。
                    # 即使校验服务报错，也记录等待时间。
                    verification_elapsed_seconds = (
                            perf_counter() - verification_started_at
                    )

                    print(
                        f"[Agent 耗时] 证据校验："
                        f"{verification_elapsed_seconds:.2f} 秒",
                        flush=True,
                    )

                # 校验正常结束后，根据结果决定是否拒答。
                if not evidence_supported:
                    return AgentRunResult(
                        answer="根据当前检索结果，无法确认答案。",
                        sources=(),
                    )

            # 查过知识库却没有引用原文时，不返回模型自由编写的答案。
            if knowledge_search_attempted and not cited_ids:
                return AgentRunResult(
                    answer="根据当前检索结果，无法确认答案。",
                    sources=(),
                )

            # 有知识库引用时，优先返回已核验的原文。
            # 普通聊天先保留模型原本的回答。
            safe_answer = decision.answer

            # 只有引用知识库时，才改为返回核验过的原文。
            if cited_ids:
                safe_answer = "根据知识库原文：\n" + "\n".join(
                    verified_quotes
                )
                if len(safe_answer) > 8000:
                    raise InvalidAgentEvidenceError(
                        "原文证据过长"
                    )

            # 只返回真实检索到、且被模型选为引用的来源。
            return AgentRunResult(
                answer=safe_answer,
                sources=tuple(
                    source
                    for source in knowledge_sources
                    if source.chunk_id in cited_ids
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
        # 检索资料只能提供证据，不能成为新申请的写入授权。
        if decision.tool_request.name == "request_ticket_closure" and knowledge_search_attempted:
            raise ApprovalAccessError("知识检索结果不能授权创建关闭申请")

        # 把本次运行共享的工具上下文交给 Dispatcher。
        # 记录工具执行前的计时读数。
        tool_started_at = perf_counter()

        try:
            # Dispatcher 根据工具名称，执行对应的工具。
            with operation_span("tool.execute", tool_name=decision.tool_request.name):
                tool_result = agent_dispatcher.dispatch_tool(
                    # 工具共用的依赖：数据库会话、向量模型和权限范围。
                    tool_context,

                    # 本次要执行的工具名称及参数。
                    decision.tool_request,
                )
        finally:
            # 即使工具执行失败，也记录已经消耗的时间。
            tool_elapsed_seconds = (
                    perf_counter() - tool_started_at
            )

            # 输出工具名称，方便区分知识检索与其他工具。
            # 不输出工具参数，避免记录用户问题等内容。
            safe_tool_name = (
                decision.tool_request.name
                if decision.tool_request.name in ("get_ticket_details", "search_knowledge", "request_ticket_closure")
                else "unknown"
            )
            print(
                f"[Agent 耗时] 工具 "
                f"{safe_tool_name}："
                f"{tool_elapsed_seconds:.2f} 秒",
                flush=True,
            )

        # 申请一旦保存，立即返回后端生成的编号，避免模型重复提交或虚报关闭。
        if isinstance(tool_result, RequestTicketClosureResult):
            return AgentRunResult(answer=tool_result.message, sources=())

        # 只有知识库工具的结果才能成为知识来源。
        if isinstance(
            tool_result,
            SearchKnowledgeToolResult,
        ):
            knowledge_search_attempted = True
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
                        # 页码来自真实工具结果，不让模型自行编造。
                        page_numbers=list(
                            match.page_numbers
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

                # 保存真实原文，供最终回答时核对引句。
                knowledge_contents[
                    match.chunk_id
                ] = match.content


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
