"""使用现有知识库和 Ollama，评估真实检索排名；默认跳过。"""

import os
from time import perf_counter

import pytest
from sqlalchemy import select

from ticketpilot.knowledge.application.retrieval import (
    MAX_RETRIEVAL_TOP_K,
    rerank_candidates,
    retrieve_knowledge,
)

from ticketpilot.agent.loop import run_agent
from ticketpilot.agent.providers.ollama import OllamaModel, OllamaModelError
from ticketpilot.agent.schemas import (
    AgentMessage,
    FinalAnswerDecision,
    SearchKnowledgeToolArguments,
    ToolCallDecision,
    ToolRequest,
)
from ticketpilot.agent.tools.knowledge import search_knowledge

from ticketpilot.core.database import SessionLocal
from ticketpilot.knowledge.application.evaluation import (
    RetrievalEvalCase,
    evaluate_retrieval_case,
)
from ticketpilot.knowledge.embeddings.ollama import OllamaEmbeddingProvider
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)



@pytest.mark.parametrize(
    # pytest 会依次把每组值传给测试函数的三个同名参数。
    ("case_id", "question", "answer_anchor"),
    [
        pytest.param(
            "workflow-direct",
            "开发一个新功能前，按学习笔记应该先确定哪些内容？",
            "先写业务流程和职责边界",
            id="workflow-direct",
        ),
        pytest.param(
            "workflow-paraphrase",
            "按照学习笔记，动手开发新功能之前要先想清楚什么？",
            "先写业务流程和职责边界",
            id="workflow-paraphrase",
        ),
        pytest.param(
            "architecture",
            "为什么 TicketPilot 采用模块化单体，而不是一开始拆成微服务？",
            "模块化单体表示",
            id="architecture",
        ),
        pytest.param(
            "retrospective",
            "学习笔记说每完成一个阶段要记录哪些复盘问题？",
            "这次完成了什么业务能力",
            id="retrospective",
        ),
    ],
)
@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
def test_study_pdf_retrieval_ranking(
    # 本题名称，用于标识评分结果。
    case_id: str,

    # 本题提交给知识库的检索问题。
    question: str,

    # 正确片段中应出现的文字，用于从数据库定位标准答案。
    answer_anchor: str,
) -> None:
    """确认已知正确片段能出现在真实检索的前五条。"""

    # 普通测试默认跳过，避免意外连接真实数据库和 Ollama。
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")

    with SessionLocal() as session:

        # 从数据库找出包含标准答案的正确片段 ID。
        expected_ids = set(
            session.scalars(
                select(KnowledgeChunkRecord.id)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeChunkRecord.document_id
                    == KnowledgeDocumentRecord.id,
                )
                .where(
                    KnowledgeDocumentRecord.source_uri
                    == "TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf",
                    KnowledgeDocumentRecord.status == "ready",
                    KnowledgeDocumentRecord.visibility == "internal",
                    KnowledgeChunkRecord.content.contains(answer_anchor),
                )
            ).all()
        )
        assert expected_ids, "未找到用于评估的 PDF 正确片段"

        # 用本题的问题和正确片段 ID 创建评估题对象。
        case = RetrievalEvalCase(
            case_id=case_id,                    # 当前测试题的名称。
            question=question,                  # 当前测试题的问题。
            relevant_chunk_ids=tuple(expected_ids),  # 人工标注的正确片段。
        )

        # 第一阶段：向量检索一次，取得与 Agent 工具相同的候选池。
        candidate_chunks = retrieve_knowledge(
            session=session,  # 当前只读查询使用的数据库会话。
            question=question,  # 当前评估题的问题。
            embedding_provider=OllamaEmbeddingProvider(),  # 生成问题向量。
            top_k=MAX_RETRIEVAL_TOP_K,  # 最多取 20 个候选片段。
            min_similarity=0.0,  # 不提前过滤低相似度片段。
            allowed_visibilities=("internal",),  # 只允许内部文档。
        )

        # 第二阶段：只改变上述候选片段的顺序，取重排后的前 5 条。
        reranked_chunks = rerank_candidates(
            question=question,  # 用同一个问题计算文字匹配程度。
            candidates=candidate_chunks,  # 必须用刚才那批候选。
            top_k=5,  # 最终只保留前 5 条。
        )

        # [:5] 是 Python 切片：取得原始向量排名的前五条。
        raw_ids = [
            chunk.chunk_id
            for chunk in candidate_chunks[:5]
        ]

        # 重排函数已经只返回前五条。
        reranked_ids = [
            chunk.chunk_id
            for chunk in reranked_chunks
        ]

        raw_score = evaluate_retrieval_case(
            case=case,  # 同一道人工作答的评估题。
            retrieved_ids=raw_ids,  # 未重排时的片段 ID 排名。
            k=5,  # 只评估前五条。
        )
        reranked_score = evaluate_retrieval_case(
            case=case,  # 仍然是同一道题。
            retrieved_ids=reranked_ids,  # 重排后的片段 ID 排名。
            k=5,  # 同样评估前五条。
        )

        print(
            f"{case_id}: "
            f"纯向量 Recall@5={raw_score.recall:.3f}, "
            f"RR@5={raw_score.reciprocal_rank:.3f}; "
            f"重排后 Recall@5={reranked_score.recall:.3f}, "
            f"RR@5={reranked_score.reciprocal_rank:.3f}"
        )

        # 只检查名次退化的“模块化单体”题，避免其他题输出过多。
        if case_id == "architecture":
            for label, chunks in (
                ("纯向量", candidate_chunks[:5]),
                ("重排后", reranked_chunks),
            ):
                print(f"{label}的前五条：")

                # enumerate() 是 Python 内置函数：
                # 同时取得从 1 开始的名次 rank 和当前片段 chunk。
                for rank, chunk in enumerate(chunks, start=1):
                    # split() 按空格和换行拆开文字；
                    # join() 再用单个空格连接，方便在一行中阅读。
                    preview = " ".join(chunk.content.split())[:180]

                    # 这里的“正确”依据本题的答案锚点所定位的片段 ID。
                    is_correct = chunk.chunk_id in expected_ids

                    print(
                        f"  第{rank}名 | "
                        f"{'正确' if is_correct else '其他'} | "
                        f"{chunk.document_title} "
                        f"片段{chunk.chunk_index} | "
                        f"{preview}"
                    )

        # 继续保护当前正式使用的“向量＋重排”结果。
        # 不断言“重排一定比纯向量好”：真实对照可能揭示它有时变差。
        assert reranked_score.recall > 0, "正确片段没有进入重排后的前五条"


@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
def test_architecture_agent_answer_diagnostic() -> None:
    """查看 Agent 是否用知识片段回答架构选择的原因。"""

    # 默认跳过，避免普通测试调用真实数据库和模型。
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")

    question = "为什么 TicketPilot 采用模块化单体，而不是一开始拆成微服务？"

    with SessionLocal() as session:
        result = run_agent(
            session=session,  # 本次 Agent 调用使用的数据库会话。
            model=OllamaModel(),  # 负责生成最终回答的本地聊天模型。
            user_message=question,  # 用户提出的架构问题。
            embedding_provider=OllamaEmbeddingProvider(),  # 生成检索向量。
            allowed_visibilities=("internal",),  # 只允许读取内部文档。
            require_knowledge_search=True,  # 后端强制先检索再回答。
        )

    print(f"Agent 回答：{result.answer}")
    print("Agent 引用的片段：")

    # 逐条显示真正被 Agent 引用的来源，不打印完整文档。
    for source in result.sources:
        print(
            f"  {source.document_title}，"
            f"片段 {source.chunk_index}，"
            f"文件 {source.source_uri}"
        )

    # 若返回无来源的“无法确认”，这里会失败并明确提醒我们。
    assert result.sources, "Agent 没有引用任何知识片段"


@pytest.mark.parametrize(
    (
        "case_id",
        "question",
        "answer_anchor",
        "match_counts",
        "expected_source_uri",
        "expected_document_title",
    ),
    [
        pytest.param(
            "architecture",
            "为什么 TicketPilot 采用模块化单体，而不是一开始拆成微服务？",
            "模块化单体表示",
            (1, 3, 5),
            "TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf",
            None,
            id="architecture",
        ),
        pytest.param(
            "workflow",
            "开发一个新功能前，按学习笔记应该先确定哪些内容？",
            "先写业务流程和职责边界",
            (3, 5),
            "TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf",
            None,
            id="workflow",
        ),
        pytest.param(
            "retrospective",
            "学习笔记说每完成一个阶段要记录哪些复盘问题？",
            "这次完成了什么业务能力",
            (3, 5),
            "TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf",
            None,
            id="retrospective",
        ),
        pytest.param(
            "contract-warranty",
            "根据维修合同，保修从什么时候开始、适用于哪类故障、持续多久？",
            "按送货日起,非人为故障对维修部份保修叁个月",
            (3, 5),
            "2323.docx",
            None,
            id="contract-warranty",
        ),
        pytest.param(
            "manual-password",
            "员工忘记密码且无法访问企业邮箱时，由谁确认身份、由谁处理，处理前还要核对什么？",
            "IT 服务台处理前必须核对员工姓名、工号和所属部门",
            (3, 5),
            None,
            "员工账号密码重置流程",
            id="manual-password",
        ),
        pytest.param(
            "markdown-rag",
            "RAG 架构设计要求失败任务具备什么能力？开发时怎样避免整体推倒重写？",
            "对失败任务进行重试和恢复",
            (3, 5),
            "RAG_ARCHITECTURE.md",
            None,
            id="markdown-rag",
        ),
    ],
)
@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
def test_context_size_diagnostic(
    # 当前评估题的名称，用于区分打印结果。
    case_id: str,

    # 原样提交给知识检索和聊天模型的问题。
    question: str,

    # 正确知识片段中必须出现的原文，用于定位标准答案。
    answer_anchor: str,

    # 本题依次交给模型的检索片段数量。
    match_counts: tuple[int, ...],

    # 文件型知识的原始文件名；手动录入文档没有文件名。
    expected_source_uri: str | None,

    # 手动录入知识的文档标题；文件型知识用文件名定位。
    expected_document_title: str | None,
) -> None:
    """比较同一问题下不同结果数量对模型回答的影响。"""

    # 这是会访问真实数据库和 Ollama 的诊断测试，普通回归默认跳过。
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")

    # 条件表达式根据来源类型决定按文件名还是按手动文档标题定位。
    # is not None 是明确检查是否传入了文件名，不把空字符串当成默认值。
    document_filter = (
        KnowledgeDocumentRecord.source_uri == expected_source_uri
        if expected_source_uri is not None
        else KnowledgeDocumentRecord.title == expected_document_title
    )

    with SessionLocal() as session:
        # 用学习笔记的原文定位标准答案，不让模型自己决定什么算正确。
        # set() 是 Python 内置函数：把查询出的 ID 去重，组成集合。
        expected_ids = set(
            session.scalars(
                select(KnowledgeChunkRecord.id)
                .join(
                    KnowledgeDocumentRecord,
                    KnowledgeChunkRecord.document_id
                    == KnowledgeDocumentRecord.id,
                )
                .where(
                    document_filter,
                    KnowledgeDocumentRecord.status == "ready",
                    KnowledgeDocumentRecord.visibility == "internal",
                    KnowledgeChunkRecord.content.contains(answer_anchor),
                )
            ).all()
        )
        assert expected_ids, f"未找到 {case_id} 的标准答案片段"

        # 每道题只检索一次，确保本题各组共享同一批、同一顺序的候选。
        full_result = search_knowledge(
            session=session,  # 本次只读检索使用的数据库会话。
            embedding_provider=OllamaEmbeddingProvider(),  # 生成问题向量。
            arguments=SearchKnowledgeToolArguments(
                query=question,  # 本题各组实验使用同一个问题。
                top_k=5,  # 先取得正式 Agent 会看到的五条。
            ),
            allowed_visibilities=("internal",),  # 只读取内部文档。
        )

    assert len(full_result.matches) == 5, "需要五条候选才能进行数量对照"

    # 模拟强制知识检索时，Agent 历史中已有的工具调用消息。
    tool_call = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="search_knowledge",  # 与正式 Agent 使用相同的工具名称。
            arguments={"query": question},  # 与真实检索使用同一个问题。
        ),
    )
    model = OllamaModel()

    for match_count in match_counts:
        # 仅改变交给模型的片段数量；片段内容和相对顺序都不变。
        # [:match_count] 是切片，取得当前要比较的前几条。
        visible_matches = full_result.matches[:match_count]
        # model_copy() 创建新的结果对象，不修改共享的五条原始结果。
        limited_result = full_result.model_copy(
            update={
                "matches": visible_matches,
                "message": f"已找到 {match_count} 条相关企业知识",
            }
        )
        visible_ids = {match.chunk_id for match in visible_matches}
        # bool() 把两个集合的交集转成 True/False：
        # 交集非空，表示标准答案片段已经进入模型上下文。
        correct_visible = bool(visible_ids & expected_ids)

        # perf_counter() 是 Python 标准库的高精度计时函数；
        # 这里用调用前后的差值记录本次模型耗时。
        started_at = perf_counter()
        try:
            decision = model.decide(
                [
                    AgentMessage(role="user", content=question),
                    AgentMessage(
                        role="assistant",
                        content=tool_call.model_dump_json(),
                    ),
                    AgentMessage(
                        role="tool",
                        content=limited_result.model_dump_json(),
                    ),
                ],
                allow_tool_calls=False,  # 检索之后只允许最终回答。
            )
        except OllamaModelError as error:
            # 超时也属于诊断结果；继续比较其余组，不把它伪装成拒答。
            elapsed_seconds = perf_counter() - started_at
            print(
                f"{case_id} 前 {match_count} 条："
                f"正确片段可见={correct_visible}，"
                f"模型调用失败={type(error).__name__}，"
                f"耗时={elapsed_seconds:.1f} 秒",
                flush=True,
            )
            continue

        elapsed_seconds = perf_counter() - started_at
        # isinstance() 是 Python 内置函数，用来检查决定的实际类型。
        assert isinstance(decision, FinalAnswerDecision)
        cited_ids = set(decision.cited_chunk_ids)
        print(
            f"{case_id} 前 {match_count} 条："
            f"正确片段可见={correct_visible}，"
            f"证据足够={decision.evidence_is_sufficient}，"
            f"引用正确片段={bool(cited_ids & expected_ids)}，"
            # len() 是 Python 内置函数，返回引用 ID 的数量。
            f"引用数={len(cited_ids)}，耗时={elapsed_seconds:.1f} 秒",
            flush=True,
        )
        # decide() 的 answer 只是模型草稿；有引用时 AgentLoop 最终返回核验原文。
        print(f"  模型草稿（非最终 API 回答）：{decision.answer[:200]}", flush=True)


@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
def test_contract_warranty_final_agent_answer() -> None:
    """确认正式 Agent 不会把错误的保修起算点返回给用户。"""

    # 真实数据库与 Ollama 仅在明确启用端到端评测时访问。
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")

    question = "根据维修合同，保修从什么时候开始、适用于哪类故障、持续多久？"
    with SessionLocal() as session:
        # 这里运行完整 AgentLoop，而不是直接读取模型的决定草稿。
        result = run_agent(
            session=session,  # 本次调用使用的数据库会话。
            model=OllamaModel(),  # 生成答案与引用的本地聊天模型。
            user_message=question,  # 与上方诊断测试相同的合同问题。
            embedding_provider=OllamaEmbeddingProvider(),  # 生成检索向量。
            allowed_visibilities=("internal",),  # 只检索内部文档。
            require_knowledge_search=True,  # 必须先查知识库。
        )

    print(f"正式 Agent 回答：{result.answer}", flush=True)
    print(
        f"引用来源：{[source.source_uri for source in result.sources]}",
        flush=True,
    )

    # 这两个断言是评测集的人工标准答案，不写进正式业务代码。
    # 如果 Agent 拒答，本测试会失败，提醒我们继续诊断而不是视作答对。
    assert any(source.source_uri == "2323.docx" for source in result.sources)
    assert "送货日起" in result.answer
    assert "维修日期起" not in result.answer
