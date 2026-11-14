"""
实现 Agent 使用的企业知识检索工具。

正常业务流程：
模型请求调用 search_knowledge
→ Dispatcher 验证工具参数
→ 本工具接收 Session 和 EmbeddingProvider
→ 调用 Retrieval Service
→ Retrieval Service 生成问题向量
→ PostgreSQL pgvector 查询相关知识
→ 转换成 Agent 可以阅读的安全结果
→ 返回 Agent Loop
→ 模型根据知识片段生成最终回答

职责边界：
- Dispatcher：识别工具名称并验证参数。
- 本工具：把 Agent 参数转换成 Retrieval Service 调用。
- Retrieval Service：编排向量生成和知识检索。
- Repository：执行真实 pgvector 查询。
- Agent 模型：根据检索结果组织自然语言回答。
"""

# Session 表示当前请求使用的数据库会话。
from sqlalchemy.orm import Session

# 导入知识检索工具的参数和返回模型。
from ticketpilot.agent.schemas import (
    KnowledgeToolMatch,
    SearchKnowledgeToolArguments,
    SearchKnowledgeToolResult,
)

# 导入统一的 Embedding Provider 接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 导入已经完成并测试过的知识检索服务。
from ticketpilot.knowledge.application.retrieval import (
    retrieve_knowledge,
    MAX_RETRIEVAL_TOP_K,
    rerank_candidates,
)


# 这个函数负责根据 Agent 提供的问题搜索企业知识库。
def search_knowledge(
    # 当前请求独享的数据库 Session。
    session: Session,

    # 用于把问题转换成向量的模型。
    embedding_provider: EmbeddingProvider,

    # 已经经过 Pydantic 验证的工具参数。
    arguments: SearchKnowledgeToolArguments,

    # 星号后必须使用参数名传值。
    *,
    # 旧调用默认只能检索 internal 文档。
    allowed_visibilities: tuple[str, ...] = ("internal",),

) -> SearchKnowledgeToolResult:
    # 调用 Retrieval Service 完成向量生成和 pgvector 检索。
    # 第一阶段：按向量相似度多取一些候选。
    # 数据库仍会先执行状态、权限和最低相似度过滤。
    candidate_chunks = retrieve_knowledge(
        session=session,
        question=arguments.query,
        embedding_provider=embedding_provider,
        top_k=MAX_RETRIEVAL_TOP_K,  # 当前上限是 20
        min_similarity=arguments.min_similarity,
        allowed_visibilities=allowed_visibilities,
    )

    # 第二阶段：合并向量排名和文字排名，
    # 最终只把用户要求的 top_k 条交给 Agent。
    retrieved_chunks = rerank_candidates(
        question=arguments.query,
        candidates=candidate_chunks,
        top_k=arguments.top_k,
    )

    # 把内部检索结果转换成允许交给模型的数据。
    matches = [
        KnowledgeToolMatch(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_title=(
                chunk.document_title
            ),
            document_version=(
                chunk.document_version
            ),
            source_type=chunk.source_type,
            source_uri=chunk.source_uri,
            chunk_index=chunk.chunk_index,

            # 保留知识片段原文，
            # 不在工具层修改空格和内容。
            content=chunk.content,

            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            # 将检索结果中的只读页码转成工具结果的列表。
            page_numbers=list(
                chunk.page_numbers
            ),

            # 防止浮点误差产生略大于 1 的值。
            similarity=max(
                0.0,
                min(
                    1.0,
                    chunk.similarity,
                ),
            ),
        )
        for chunk in retrieved_chunks
    ]

    # 没有找到符合最低相似度的知识片段。
    if not matches:
        return SearchKnowledgeToolResult(
            found=False,
            message="没有找到相关企业知识",
            query=arguments.query,
            matches=[],
        )

    # 找到知识时返回全部安全匹配结果。
    return SearchKnowledgeToolResult(
        found=True,
        message=(
            f"已找到 {len(matches)} 条相关企业知识"
        ),
        query=arguments.query,
        matches=matches,
    )