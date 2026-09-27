"""
测试知识库 API 的 Pydantic Schema。

测试目标：
- 合法标题和正文能够通过验证。
- 原始正文首尾空白不会被 Schema 修改。
- 非法请求会产生 Pydantic ValidationError。
"""

# pytest 用来验证预期异常和参数化测试。
import pytest

# ValidationError 表示 Pydantic 数据验证失败。
from pydantic import ValidationError

# 导入需要测试的请求 Schema。
from ticketpilot.knowledge.schemas import (
    KnowledgeDocumentCreateRequest,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResultResponse,
)


# 这个测试负责验证合法请求能够通过并保留原始文本。
def test_create_request_accepts_valid_document() -> None:
    # 正文故意保留首尾换行和空格。
    original_content = (
        "\n  用户可以通过邮箱重置密码。  \n"
    )

    # 创建并验证请求对象。
    request = KnowledgeDocumentCreateRequest(
        title="密码重置说明",
        content=original_content,
    )

    # 标题应该保持不变。
    assert request.title == "密码重置说明"

    # Schema 不负责清洗，所以正文原始空白必须保留。
    assert request.content == original_content

@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
    ),
    [
        # 标题不能只包含空白。
        (
            "title",
            " \t\n ",
        ),

        # 正文不能只包含空白。
        (
            "content",
            " \t\n ",
        ),
    ],
)
# 这个测试负责验证纯空白标题或正文会被拒绝。
def test_create_request_rejects_blank_text(
    field_name: str,
    invalid_value: str,
) -> None:
    # 先准备一份正常请求数据。
    request_data = {
        "title": "密码重置说明",
        "content": "用户可以通过邮箱重置密码。",
    }

    # 根据当前参数替换标题或者正文。
    request_data[field_name] = invalid_value

    # Pydantic 应该拒绝这份请求。
    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentCreateRequest(
            **request_data
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
    ),
    [
        # 数据库标题字段最多保存 200 个字符。
        pytest.param(
            "title",
            "标" * 201,
            id="title_too_long",
        ),

        # 当前同步接口最多接收 100000 个字符。
        pytest.param(
            "content",
            "文" * 100_001,
            id="content_too_long",
        ),
    ],
)
# 这个测试负责验证超过最大长度的字段会被拒绝。
def test_create_request_rejects_oversized_text(
    field_name: str,
    invalid_value: str,
) -> None:
    request_data = {
        "title": "密码重置说明",
        "content": "用户可以通过邮箱重置密码。",
    }

    request_data[field_name] = invalid_value

    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentCreateRequest(
            **request_data
        )


@pytest.mark.parametrize(
    "extra_field",
    [
        "created_by",
        "source_type",
        "status",
        "embedding_model",
        "embedding_dimensions",
    ],
)
# 这个测试负责验证客户端不能控制内部业务字段。
def test_create_request_rejects_extra_fields(
    extra_field: str,
) -> None:
    request_data = {
        "title": "密码重置说明",
        "content": "用户可以通过邮箱重置密码。",

        # 模拟客户端伪造内部字段。
        extra_field: "伪造的数据",
    }

    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentCreateRequest(
            **request_data
        )

# 这个测试负责验证检索请求会使用默认参数。
def test_search_request_uses_default_values() -> None:
    request = KnowledgeSearchRequest(
        query="如何重置密码？"
    )

    assert request.query == "如何重置密码？"

    # 没有提交 top_k 时，默认返回最多 5 条。
    assert request.top_k == 5

    # 没有提交最低相似度时，默认使用 0。
    assert request.min_similarity == 0.0


@pytest.mark.parametrize(
    "invalid_request",
    [
        pytest.param(
            {
                "query": "   ",
            },
            id="blank_query",
        ),
        pytest.param(
            {
                "query": "问" * 2001,
            },
            id="query_too_long",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "top_k": 0,
            },
            id="top_k_too_small",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "top_k": 21,
            },
            id="top_k_too_large",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "min_similarity": -0.01,
            },
            id="similarity_too_small",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "min_similarity": 1.01,
            },
            id="similarity_too_large",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "unknown_field": "不允许的字段",
            },
            id="extra_field",
        ),
    ],
)
# 这个测试负责验证不合法的检索请求会被拒绝。
def test_search_request_rejects_invalid_data(
    invalid_request: dict[
        str,
        object,
    ],
) -> None:
    with pytest.raises(
        ValidationError
    ):
        KnowledgeSearchRequest(
            **invalid_request
        )

# 这个测试负责验证检索响应能够转换成公开 JSON 数据。
def test_search_response_serializes_nested_results() -> None:
    # 创建一条知识检索结果。
    result = KnowledgeSearchResultResponse(
        chunk_id="chunk-001",
        document_id="document-001",
        document_title="密码重置说明",
        document_version=1,

        # Pydantic 会把字符串转换成 KnowledgeSourceType。
        source_type="manual",

        source_uri=None,
        chunk_index=0,
        content="用户可以通过注册邮箱重置密码。",
        start_offset=0,
        end_offset=17,
        similarity=0.92,
    )

    # 创建包含检索结果的完整响应。
    response = KnowledgeSearchResponse(
        query="如何重置密码？",
        result_count=1,
        results=[
            result,
        ],
    )

    # mode="json" 会把枚举等 Python 对象转换成 JSON 数据。
    response_data = response.model_dump(
        mode="json"
    )

    assert response_data == {
        "query": "如何重置密码？",
        "result_count": 1,
        "results": [
            {
                "chunk_id": "chunk-001",
                "document_id": "document-001",
                "document_title": "密码重置说明",
                "document_version": 1,
                "source_type": "manual",
                "source_uri": None,
                "chunk_index": 0,
                "content": (
                    "用户可以通过注册邮箱重置密码。"
                ),
                "start_offset": 0,
                "end_offset": 17,
                "similarity": 0.92,
            },
        ],
    }