"""
实现 RAG 的知识检索服务。

正常业务流程：
用户问题
→ 验证问题、top_k 和最低相似度
→ EmbeddingProvider 把问题转换成 1024 维向量
→ Repository 使用 pgvector 查询相似片段
→ 只保留状态为 ready 的知识文档
→ 按相似度从高到低排列
→ 返回最相关的知识片段

示例：
“忘记密码怎么办？”
→ 问题向量
→ 与 knowledge_chunks.embedding 计算余弦距离
→ 找到“用户可以通过注册邮箱重置密码”
→ 返回片段文字、所属文档和相似度

职责边界：
- Retrieval Service：验证参数、生成问题向量、编排检索。
- Repository：执行 PostgreSQL 与 pgvector 查询。
- Embedding Provider：把问题转换成向量。
- Agent：使用检索结果组织最终回答。

异常流程：
- 问题为空：抛出查询参数异常。
- top_k 或相似度范围错误：抛出查询参数异常。
- Ollama 调用失败：转换成知识检索异常。
- PostgreSQL 查询失败：转换成知识检索异常。

当前第一阶段：
- 使用 cosine distance（余弦距离）。
- 默认返回最相关的 5 个片段。
- 只查询 ready 状态的文档。
- 不在 Retrieval Service 中生成最终自然语言回答。

完整 RAG 流程：
用户问题
→ Retrieval Service
→ Embedding Provider
→ pgvector Repository
→ 相关知识片段
→ Agent / LLM
→ 最终回答
"""


# dataclass 用来定义只负责携带检索结果的数据对象。
from dataclasses import dataclass

import re

# 导入知识文档来源类型。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
)

# Session 表示本次知识检索使用的数据库会话。
from sqlalchemy.orm import Session

# 导入数据库固定使用的向量维度，目前是 1024。
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入向量模型接口和可能出现的异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
    EmbeddingProviderError,
    InvalidEmbeddingInputError,
    InvalidEmbeddingResponseError,
)

# 导入知识库 Repository 的查询函数和统一异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    search_ready_chunks_by_cosine,
)


# 默认返回最多 5 个相关知识片段。
DEFAULT_RETRIEVAL_TOP_K = 5

# 防止一次查询返回过多内容并占用大量模型上下文。
MAX_RETRIEVAL_TOP_K = 20

# 默认只接受非负的余弦相似度。
DEFAULT_MIN_SIMILARITY = 0.0


# 表示用户提交的检索参数不合法。
class InvalidKnowledgeQueryError(ValueError):
    """知识查询问题或检索参数不合法。"""


# 表示向量模型或数据库导致知识检索失败。
class KnowledgeRetrievalError(RuntimeError):
    """知识检索流程执行失败。"""


# 这个类负责保存一条可以交给 Agent 使用的知识检索结果。
@dataclass(
    frozen=True,
    slots=True,
)
class RetrievedKnowledgeChunk:
    # 知识片段 ID。
    chunk_id: str

    # 片段所属文档 ID。
    document_id: str

    # 所属文档标题，用于回答时说明知识来源。
    document_title: str

    # 当前文档版本。
    document_version: int

    # manual、file 或 url。
    source_type: KnowledgeSourceType

    # 文件名或网页地址；手动文档通常为空。
    source_uri: str | None

    # 片段在文档中的顺序。
    chunk_index: int

    # 真正交给 Agent 的知识片段文字。
    content: str

    # 片段在清洗后正文中的位置。
    start_offset: int
    end_offset: int

    # 这个检索片段对应的 PDF 原始页码。
    # 非 PDF 文档使用空元组。
    page_numbers: tuple[int, ...]

    # PostgreSQL 计算出的余弦距离。
    # 距离越小，代表内容越相似。
    cosine_distance: float

    # 这个属性负责把余弦距离转换成更容易理解的相似度。
    @property
    def similarity(self) -> float:
        # cosine similarity = 1 - cosine distance
        return 1.0 - self.cosine_distance

def _character_trigrams(text: str) -> set[str]:
    """把文字拆成连续的三个字符，用于候选片段重排。"""

    # casefold() 统一英文字母大小写。
    # re.sub() 删除空格、标点和下划线；中文字符会保留。
    normalized = re.sub(
        r"[\W_]+",
        "",
        text.casefold(),
    )

    # 不足三个字符时，无法组成三字片段。
    if len(normalized) < 3:
        return set()

    # 例如“完成新功能”得到：
    # “完成新”“成新功”“新功能”。
    # set 会去掉重复项。
    return {
        normalized[index:index + 3]
        for index in range(len(normalized) - 2)
    }


def rerank_candidates(
        *,
        question: str,
        candidates: tuple[RetrievedKnowledgeChunk, ...],
        top_k: int,
) -> tuple[RetrievedKnowledgeChunk, ...]:
    """合并向量排名和文字排名。"""

    # 没有候选片段，就没有重排的对象。
    if not candidates:
        return candidates[:top_k]

    # 文档标题用于定位文档，不应重复算作“问题与正文匹配”。
    question_without_titles = question

    # set 会去重：同一篇文档的多个片段只需处理一次标题。
    for title in {chunk.document_title for chunk in candidates}:
        if title:
            # str.replace() 将问题中出现的标题删掉；找不到时保持原样。
            question_without_titles = question_without_titles.replace(
                title,
                "",
            )

    question_parts = _character_trigrams(question_without_titles)

    # 问题太短、无法拆成三字片段时，保持原向量顺序。
    if not question_parts:
        return candidates[:top_k]

    # 给每个候选片段配上原始下标，供后面的排序和排名计算使用。
    indexed_candidates = list(enumerate(candidates))

    # 计算一条候选片段与问题共有多少个三字片段。
    def count_text_overlap(
            item: tuple[int, RetrievedKnowledgeChunk],
    ) -> int:
        # item 是（原来的位置，知识片段）。
        # 这里暂时只需要片段正文，所以位置用 _original_index 表示不用。
        _original_index, chunk = item

        # 把片段正文也拆成三字片段。
        chunk_parts = _character_trigrams(chunk.content)

        # & 取两个集合共同拥有的部分。
        common_parts = question_parts & chunk_parts

        # 共同部分越多，文字匹配程度越高。
        return len(common_parts)


    # sorted() 根据上面函数返回的数字重新排序。
    # reverse=True 表示数字大的排前面。
    text_order = sorted(
        indexed_candidates,
        key=count_text_overlap,
        reverse=True,
    )

    # 记录“原来的位置 → 文字排序后的名次”。
    text_ranks: dict[int, int] = {}

    for rank, (original_index, _chunk) in enumerate(
        text_order,
        start=1,
    ):
        text_ranks[original_index] = rank

    def combined_score(
        item: tuple[int, RetrievedKnowledgeChunk],
    ) -> float:
        # 原位置从 0 开始，排序名次从 1 开始。
        vector_rank = item[0] + 1
        text_rank = text_ranks[item[0]]

        # RRF 合并两个名次；60 用来缓和名次差距。
        # 分数越大，合并后的排名越靠前。
        return (
            1 / (60 + vector_rank)
            + 1 / (60 + text_rank)
        )

    ranked = sorted(
        indexed_candidates,
        key=combined_score,
        reverse=True,
    )

    # 如果文字匹配有明确且唯一的领先者，把它放到最前。
    # 至少匹配两个三字片段，避免只有一个偶然相同的词就抢占首位。
    if len(text_order) >= 2:
        best_text_item = text_order[0]
        best_overlap = count_text_overlap(best_text_item)
        second_overlap = count_text_overlap(text_order[1])

        if best_overlap >= 2 and best_overlap > second_overlap:
            ranked.remove(best_text_item)
            ranked.insert(0, best_text_item)

    # 只取前 top_k 条，再去掉附加的原位置。
    return tuple(
        chunk
        for _index, chunk in ranked[:top_k]
    )

# 这个函数负责根据用户问题检索最相关的知识片段。
def retrieve_knowledge(
    *,
    # 当前请求独享的数据库 Session。
    session: Session,

    # 用户提交的自然语言问题。
    question: str,

    # 负责把问题转换成向量的模型。
    embedding_provider: EmbeddingProvider,

    # 最多返回多少个相关知识片段。
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,

    # 结果允许的最低相似度。
    min_similarity: float = DEFAULT_MIN_SIMILARITY,

    # 本次允许检索的文档类型；默认只检索 internal。
    allowed_visibilities: tuple[str, ...] = ("internal",),
) -> tuple[
    RetrievedKnowledgeChunk,
    ...,
]:
    """把用户问题转换成向量并检索相关知识片段。"""

    # 删除问题开头和结尾多余的空格、换行。
    normalized_question = question.strip()

    # 清理后没有内容，说明用户没有提供有效问题。
    if not normalized_question:
        raise InvalidKnowledgeQueryError(
            "知识查询问题不能为空"
        )

    # top_k 最少是 1，最多不能超过系统规定的 20。
    if not (
        1
        <= top_k
        <= MAX_RETRIEVAL_TOP_K
    ):
        raise InvalidKnowledgeQueryError(
            "top_k 必须在 1 到 20 之间"
        )

    # 当前使用的余弦相似度范围是 0 到 1。
    if not (
        0.0
        <= min_similarity
        <= 1.0
    ):
        raise InvalidKnowledgeQueryError(
            "min_similarity 必须在 0 到 1 之间"
        )

    # 开始生成用户问题对应的查询向量。
    try:
        # Embedding Provider 使用批量接口，
        # 即使只有一个问题，也需要放进列表中。
        embedding_batch = (
            embedding_provider.embed_texts(
                [normalized_question]
            )
        )

        # 输入一个问题，必须正好返回一个向量。
        if embedding_batch.count != 1:
            raise InvalidEmbeddingResponseError(
                "问题向量数量必须是 1"
            )

        # 模型声明的维度必须符合数据库的 Vector(1024)。
        if (
            embedding_batch.dimensions
            != EMBEDDING_DIMENSIONS
        ):
            raise InvalidEmbeddingResponseError(
                "问题向量维度与数据库结构不一致"
            )

        # 从批量结果中取得用户问题对应的第一个向量。
        query_embedding = (
            embedding_batch.vectors[0]
        )

        # 不仅要检查声明的维度，
        # 还要检查实际向量是否真的包含 1024 个数值。
        if (
            len(query_embedding)
            != embedding_batch.dimensions
        ):
            raise InvalidEmbeddingResponseError(
                "实际问题向量维度与声明维度不一致"
            )

    # 捕获输入异常、Ollama 调用异常和向量结果异常。
    except (
        InvalidEmbeddingInputError,
        EmbeddingProviderError,
    ) as error:
        # 对外统一转换成知识检索异常，
        # 同时通过 from error 保留真正的底层原因。
        raise KnowledgeRetrievalError(
            "知识查询向量生成失败"
        ) from error

    # 开始调用 Repository 查询相似知识片段。
    try:
        search_rows = (
            search_ready_chunks_by_cosine(
                # 当前请求使用的数据库 Session。
                session,

                # 用户问题对应的 1024 维向量。
                query_embedding=query_embedding,

                # 只比较由同一个模型生成的文档向量。
                embedding_model=(
                    embedding_batch.model_name
                ),

                # 只比较维度相同的向量。
                embedding_dimensions=(
                    embedding_batch.dimensions
                ),

                # 最多返回多少条结果。
                top_k=top_k,

                # 过滤低于这个相似度的结果。
                min_similarity=min_similarity,

                # 把权限范围交给数据库查询。
                allowed_visibilities=allowed_visibilities,
            )
        )

    # Repository 已经把底层数据库错误转换成这个异常。
    except KnowledgeRepositoryError as error:
        # 数据库查询失败可能让当前事务进入错误状态，
        # rollback 可以让 Session 恢复到可用状态。
        session.rollback()

        # 对上层统一暴露知识检索异常。
        raise KnowledgeRetrievalError(
            "知识片段检索失败"
        ) from error

    # 把 Repository 返回的数据库查询结果，
    # 转换成 Retrieval Service 对外返回的业务对象。
    try:
        return tuple(
            RetrievedKnowledgeChunk(
                # 当前知识片段的 ID。
                chunk_id=row.chunk_id,

                # 片段所属知识文档的 ID。
                document_id=row.document_id,

                # 文档标题用于展示知识来源。
                document_title=(
                    row.document_title
                ),

                # 保存当前知识文档的版本号。
                document_version=(
                    row.document_version
                ),

                # 把数据库字符串转换成枚举对象。
                source_type=KnowledgeSourceType(
                    row.source_type
                ),

                # 文件名、网页地址或者空值。
                source_uri=row.source_uri,

                # 当前片段在文档中的顺序。
                chunk_index=row.chunk_index,

                # 真正交给 Agent 使用的知识内容。
                content=row.content,

                # 当前片段在清洗后正文中的开始位置。
                start_offset=row.start_offset,

                # 当前片段在清洗后正文中的结束位置。
                end_offset=row.end_offset,

                # Repository 已经把数据库列表转换成页码元组。
                page_numbers=row.page_numbers,

                # 数据库计算出的余弦距离。
                cosine_distance=(
                    row.cosine_distance
                ),
            )
            # 把每一个数据库查询结果都转换一次。
            for row in search_rows
        )

    # 如果数据库中的来源类型不是 manual、file 或 url，
    # KnowledgeSourceType() 就会抛出 ValueError。
    except ValueError as error:
        raise KnowledgeRetrievalError(
            "知识检索结果包含无效数据"
        ) from error