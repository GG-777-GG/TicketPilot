"""
测试知识库 API 的 Pydantic Schema。

测试目标：
- 合法标题和正文能够通过验证。
- 原始正文首尾空白不会被 Schema 修改。
- 非法请求会产生 Pydantic ValidationError。
"""

# pytest 用来验证预期异常和参数化测试。
import pytest

# datetime 用来构造响应 Schema 中的时间字段。
from datetime import (
    UTC,
    datetime,
)

# ValidationError 表示 Pydantic 数据验证失败。
from pydantic import ValidationError

# 导入需要测试的请求 Schema。
from ticketpilot.knowledge.api.schemas import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDetailResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentTitleUpdateRequest,
    KnowledgeDocumentUpdateRequest,
    KnowledgeDocumentVersionDetailResponse,
    KnowledgeDocumentVersionListResponse,
    KnowledgeDocumentVersionResponse,
    KnowledgeDocumentVersionRestoreRequest,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResultResponse,
)


# 这个测试负责验证更新请求继承了创建请求的字段和校验规则。
def test_update_request_inherits_document_validation() -> None:
    request = KnowledgeDocumentUpdateRequest(
        title="新版密码重置说明",
        content="新版流程要求员工先验证企业邮箱。",
        expected_version=1,
    )

    assert request.title == "新版密码重置说明"
    assert request.content == (
        "新版流程要求员工先验证企业邮箱。"
    )
    assert request.expected_version == 1

    # 继承的纯空白校验仍然必须生效。
    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentUpdateRequest(
            title="   ",
            content="有效正文",
            expected_version=1,
        )

    # 继承的 extra='forbid' 也必须拒绝未知字段。
    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentUpdateRequest(
            title="有效标题",
            content="有效正文",
            expected_version=1,
            version=99,
        )

    # 版本号必须从 1 开始。
    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentUpdateRequest(
            title="有效标题",
            content="有效正文",
            expected_version=0,
        )


# 这个测试负责确认只改标题的请求不能携带正文。
def test_title_update_request_accepts_only_title_and_version() -> None:
    request = KnowledgeDocumentTitleUpdateRequest(
        title="  维修合同  ",
        expected_version=3,
    )

    assert request.title == "维修合同"
    assert request.expected_version == 3
    assert "content" not in request.model_dump()

    invalid_requests = (
        {"title": "   ", "expected_version": 3},
        {"title": "A" * 201, "expected_version": 3},
        {"title": "维修合同", "expected_version": 0},
        {"title": "维修合同", "expected_version": 3, "content": "误传的正文"},
    )
    for invalid_request in invalid_requests:
        with pytest.raises(ValidationError):
            KnowledgeDocumentTitleUpdateRequest.model_validate(invalid_request)


# 这个测试负责验证历史版本恢复请求只接收合法预期版本号。
def test_document_version_restore_request_validates_expected_version() -> None:
    request = KnowledgeDocumentVersionRestoreRequest(
        expected_version=3
    )

    assert request.expected_version == 3

    # expected_version 必须存在并且从 1 开始。
    for invalid_request in (
        {},
        {
            "expected_version": 0,
        },
        {
            "expected_version": 1,
            "restored_by": "forged-user",
        },
    ):
        with pytest.raises(
            ValidationError
        ):
            KnowledgeDocumentVersionRestoreRequest(
                **invalid_request
            )


# 这个测试负责验证详情响应能够同时保存公共字段和详情字段。
def test_document_detail_response_contains_detail_fields() -> None:
    response = KnowledgeDocumentDetailResponse(
        id="document-001",
        title="密码重置说明",
        raw_content="  用户可以重置密码。  ",
        cleaned_content="用户可以重置密码。",
        source_type="manual",
        source_uri=None,
        status="ready",
        visibility="restricted",
        processing_stage=None,
        chunk_count=1,
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimensions=1024,
        cleaner_version="cleaner-v1",
        chunker_version="chunker-v1",
        version=1,
        created_by="user-001",
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        updated_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    # 从父类继承的公共字段仍然存在。
    assert response.id == "document-001"
    assert response.chunk_count == 1
    assert response.visibility == "restricted"

    # 详情接口新增的字段也能够正确保存。
    assert response.raw_content == "  用户可以重置密码。  "
    assert response.cleaned_content == "用户可以重置密码。"
    assert response.processing_stage is None
    assert response.cleaner_version == "cleaner-v1"
    assert response.chunker_version == "chunker-v1"


# 这个测试负责防止 API 忘传可见性时悄悄显示为 internal。
def test_document_response_requires_explicit_visibility() -> None:
    response_data = {
        "id": "document-001",
        "title": "受限说明",
        "source_type": "manual",
        "source_uri": None,
        "status": "ready",
        "chunk_count": 1,
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_dimensions": 1024,
        "version": 1,
        "created_by": "user-001",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }

    with pytest.raises(ValidationError):
        KnowledgeDocumentResponse.model_validate(response_data)


# 这个测试负责验证创建请求默认使用 internal，可显式选择 restricted。
def test_create_request_validates_visibility() -> None:
    default_request = KnowledgeDocumentCreateRequest(
        title="普通说明",
        content="所有员工可阅读。",
    )
    restricted_request = KnowledgeDocumentCreateRequest(
        title="受限说明",
        content="仅主管和管理员可阅读。",
        visibility="restricted",
    )

    assert default_request.visibility == "internal"
    assert restricted_request.visibility == "restricted"

    with pytest.raises(ValidationError):
        KnowledgeDocumentCreateRequest(
            title="非法说明",
            content="不允许公开给所有人。",
            visibility="public",
        )


# 这个测试负责防止内容更新请求顺带修改文档权限。
def test_update_request_rejects_visibility() -> None:
    with pytest.raises(ValidationError):
        KnowledgeDocumentUpdateRequest(
            title="新版说明",
            content="更新后的正文。",
            expected_version=1,
            visibility="internal",
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
        page_numbers=[],
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
                "page_numbers": [],
                "similarity": 0.92,
            },
        ],
    }


# 这个测试负责验证历史版本分页响应能够生成安全 JSON。
def test_document_version_list_response_serializes_summaries() -> None:
    version = KnowledgeDocumentVersionResponse(
        version=2,
        title="第二版密码说明",
        source_type="manual",
        source_uri=None,
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimensions=1024,
        cleaner_version="cleaner-v1",
        chunker_version="chunker-v1",
        created_by="user-001",
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        superseded_by="admin-001",
        superseded_at=datetime(
            2026,
            1,
            2,
            tzinfo=UTC,
        ),
    )
    response = KnowledgeDocumentVersionListResponse(
        document_id="document-001",
        current_version=3,
        items=[
            version,
        ],
        total=2,
        limit=20,
        offset=0,
    )

    response_data = response.model_dump(
        mode="json"
    )

    assert response_data["document_id"] == "document-001"
    assert response_data["current_version"] == 3
    assert response_data["items"][0]["version"] == 2
    assert response_data["items"][0]["source_type"] == (
        "manual"
    )
    assert "raw_content" not in response_data["items"][0]
    assert "cleaned_content" not in response_data["items"][0]


@pytest.mark.parametrize(
    "invalid_data",
    [
        pytest.param(
            {
                "current_version": 0,
            },
            id="current-version-too-small",
        ),
        pytest.param(
            {
                "total": -1,
            },
            id="negative-total",
        ),
        pytest.param(
            {
                "limit": 0,
            },
            id="limit-too-small",
        ),
        pytest.param(
            {
                "limit": 101,
            },
            id="limit-too-large",
        ),
        pytest.param(
            {
                "offset": -1,
            },
            id="negative-offset",
        ),
    ],
)
# 这个测试负责验证历史版本分页响应拒绝非法数值。
def test_document_version_list_response_rejects_invalid_values(
    invalid_data: dict[str, int],
) -> None:
    response_data = {
        "document_id": "document-001",
        "current_version": 2,
        "items": [],
        "total": 1,
        "limit": 20,
        "offset": 0,
        **invalid_data,
    }

    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentVersionListResponse(
            **response_data
        )


# 这个测试负责验证历史版本号必须从 1 开始。
def test_document_version_response_rejects_invalid_version() -> None:
    with pytest.raises(
        ValidationError
    ):
        KnowledgeDocumentVersionResponse(
            version=0,
            title="非法历史版本",
            source_type="manual",
            source_uri=None,
            embedding_model="qwen3-embedding:0.6b",
            embedding_dimensions=1024,
            cleaner_version="cleaner-v1",
            chunker_version="chunker-v1",
            created_by="user-001",
            created_at=datetime(
                2026,
                1,
                1,
                tzinfo=UTC,
            ),
            superseded_by="admin-001",
            superseded_at=datetime(
                2026,
                1,
                2,
                tzinfo=UTC,
            ),
        )


# 这个测试负责验证历史版本详情继承摘要并增加正文。
def test_document_version_detail_response_contains_full_content() -> None:
    detail = KnowledgeDocumentVersionDetailResponse(
        version=1,
        title="第一版密码说明",
        source_type="manual",
        source_uri=None,
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimensions=1024,
        cleaner_version="cleaner-v1",
        chunker_version="chunker-v1",
        created_by="user-001",
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        superseded_by="admin-001",
        superseded_at=datetime(
            2026,
            1,
            2,
            tzinfo=UTC,
        ),
        raw_content="  用户最初提交的正文。  ",
        cleaned_content="用户最初提交的正文。",
    )

    response_data = detail.model_dump(
        mode="json"
    )

    # 父类中的摘要字段仍然存在。
    assert response_data["version"] == 1
    assert response_data["title"] == "第一版密码说明"

    # 详情类新增的完整正文也存在。
    assert response_data["raw_content"] == (
        "  用户最初提交的正文。  "
    )
    assert response_data["cleaned_content"] == (
        "用户最初提交的正文。"
    )
