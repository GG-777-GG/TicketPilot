# 导入 pytest，用来验证代码是否抛出预期异常
import pytest

# 导入联合类型验证器和数据验证异常
from pydantic import (
    TypeAdapter,
    ValidationError,
)

# 导入需要测试的 Agent 数据模型
from ticketpilot.agent.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    FinalAnswerDecision,
    GetTicketToolArguments,
    ModelDecision,
    ToolCallDecision,
)

# 为 ModelDecision 联合类型创建 Pydantic 验证器
MODEL_DECISION_ADAPTER = TypeAdapter(
    ModelDecision,
)

# 验证合法的工单 ID 可以通过检查
def test_get_ticket_tool_arguments_accepts_valid_id() -> None:
    # 模拟 AI 生成一个两端带空格的工单 ID
    arguments = GetTicketToolArguments(
        ticket_id="  ticket-001  ",
    )

    # ConfigDict 应该自动删除 ID 两端的空格
    assert arguments.ticket_id == "ticket-001"


# 验证全部为空格的工单 ID 会被拒绝
def test_get_ticket_tool_arguments_rejects_blank_id() -> None:
    # 表示下面的代码必须抛出 Pydantic 验证异常
    with pytest.raises(
        ValidationError,
    ):
        # 空格被删除后会变成空字符串
        GetTicketToolArguments(
            ticket_id="   ",
        )


# 验证 AI 生成的未知参数会被拒绝
def test_get_ticket_tool_arguments_rejects_extra_fields() -> None:
    # 模拟 AI 生成的原始工具参数
    invalid_arguments = {
        # 这是工具允许接收的参数
        "ticket_id": "ticket-001",

        # 这是工具没有声明的危险参数
        "delete_ticket": True,
    }

    # extra="forbid" 应该使下面的验证失败
    with pytest.raises(
        ValidationError,
    ):
        # model_validate 用来验证字典形式的原始数据
        GetTicketToolArguments.model_validate(
            invalid_arguments,
        )

# 验证模型可以选择直接回答用户
def test_model_decision_accepts_final_answer() -> None:
    # 模拟模型生成的原始结构化数据
    raw_decision = {
        "kind": "final_answer",
        "answer": "  工单当前正在处理中。  ",
    }

    # 按照 ModelDecision 联合类型进行验证
    decision = MODEL_DECISION_ADAPTER.validate_python(
        raw_decision,
    )

    # 验证器应该选择 FinalAnswerDecision
    assert isinstance(
        decision,
        FinalAnswerDecision,
    )

    # 回答两端的空格应该被自动删除
    assert decision.answer == "工单当前正在处理中。"

    # 没有使用知识库的回答默认不引用任何片段。
    assert decision.cited_chunk_ids == []
    assert decision.evidence == []
    assert decision.evidence_is_sufficient is False


# 验证最终回答可以明确声明实际引用的知识片段。
def test_model_decision_accepts_cited_chunk_ids() -> None:
    decision = MODEL_DECISION_ADAPTER.validate_python(
        {
            "kind": "final_answer",
            "answer": "密码重置请联系管理员。",
            "cited_chunk_ids": ["pdf-chunk-001"],
        }
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.cited_chunk_ids == ["pdf-chunk-001"]


# 验证最终回答可以附带片段 ID 和原文引句。
def test_model_decision_accepts_answer_evidence() -> None:
    decision = MODEL_DECISION_ADAPTER.validate_python(
        {
            "kind": "final_answer",
            "answer": "保修从送货日起计算。",
            "cited_chunk_ids": ["contract-chunk-001"],
            "evidence": [
                {"chunk_id": "contract-chunk-001", "quote": "按送货日起"},
                {"chunk_id": "contract-chunk-001", "quote": "非人为故障保修叁个月。"},
            ],
            "evidence_is_sufficient": True,
        }
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert [(item.chunk_id, item.quote) for item in decision.evidence] == [
        ("contract-chunk-001", "按送货日起"),
        ("contract-chunk-001", "非人为故障保修叁个月。"),
    ]
    assert decision.evidence_is_sufficient is True


# 验证 evidence 必须是由“片段 ID + 引句”对象组成的列表。
# 引句是否真的来自原文，由 Agent Loop 继续验证。
@pytest.mark.parametrize(
    "invalid_evidence",
    [
        {"contract-chunk-001": "送货日起"},
        [{"chunk_id": "contract-chunk-001"}],
        [{"chunk_id": "contract-chunk-001", "quote": 123}],
        [{"chunk_id": "contract-chunk-001", "quote": "   "}],
    ],
)
def test_model_decision_rejects_invalid_answer_evidence(
    invalid_evidence: object,
) -> None:
    with pytest.raises(ValidationError):
        MODEL_DECISION_ADAPTER.validate_python(
            {
                "kind": "final_answer",
                "answer": "保修三个月。",
                "evidence": invalid_evidence,
            }
        )


# 验证引用字段必须是片段 ID 列表，不能是一段随意的文本。
def test_model_decision_rejects_non_list_citations() -> None:
    with pytest.raises(ValidationError):
        MODEL_DECISION_ADAPTER.validate_python(
            {
                "kind": "final_answer",
                "answer": "密码重置请联系管理员。",
                "cited_chunk_ids": "pdf-chunk-001",
            }
        )


# 验证模型可以选择调用查询工单工具
def test_model_decision_accepts_tool_call() -> None:
    # 模拟模型生成的工具调用数据
    raw_decision = {
        "kind": "tool_call",
        "tool_request": {
            "name": "get_ticket_details",
            "arguments": {
                "ticket_id": "ticket-001",
            },
        },
    }

    # 验证原始模型数据
    decision = MODEL_DECISION_ADAPTER.validate_python(
        raw_decision,
    )

    # 验证器应该选择 ToolCallDecision
    assert isinstance(
        decision,
        ToolCallDecision,
    )

    # 验证工具名称正确
    assert (
        decision.tool_request.name
        == "get_ticket_details"
    )

    # 验证工具参数正确
    assert (
        decision.tool_request.arguments["ticket_id"]
        == "ticket-001"
    )


# 验证最终回答和工具请求不能混在一起
def test_model_decision_rejects_mixed_structure() -> None:
    # 模拟一个结构矛盾的模型决定
    invalid_decision = {
        # 声称这是最终回答
        "kind": "final_answer",

        # 提供了最终回答内容
        "answer": "准备查询工单",

        # 却又同时请求调用工具
        "tool_request": {
            "name": "get_ticket_details",
            "arguments": {
                "ticket_id": "ticket-001",
            },
        },
    }

    # 联合类型必须拒绝这个矛盾结构
    with pytest.raises(
        ValidationError,
    ):
        MODEL_DECISION_ADAPTER.validate_python(
            invalid_decision,
        )

# 验证 Agent 接口可以接收正常问题
def test_agent_chat_request_accepts_valid_message() -> None:
    # 模拟客户端提交两端带空格的问题
    request = AgentChatRequest(
        message="  帮我查询工单 ticket-001  ",
    )

    # str_strip_whitespace=True 应该删除两端空格
    assert request.message == "帮我查询工单 ticket-001"
    # 旧客户端不传 mode 时，默认继续使用自动模式。
    assert request.mode == "auto"


# 这个测试负责验证用户可以明确选择强制知识检索模式。
def test_agent_chat_request_accepts_knowledge_mode() -> None:
    request = AgentChatRequest(
        message="维修部分保修多久？",
        mode="knowledge",
    )

    assert request.mode == "knowledge"


# 这个测试负责验证未定义的对话模式会被拒绝。
def test_agent_chat_request_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        AgentChatRequest.model_validate(
            {
                "message": "维修部分保修多久？",
                "mode": "unrestricted",
            }
        )


# 验证 Agent 接口拒绝空白问题
def test_agent_chat_request_rejects_blank_message() -> None:
    # 空格删除后会变成空字符串，因此应该验证失败
    with pytest.raises(
        ValidationError,
    ):
        AgentChatRequest(
            message="   ",
        )


# 验证 Agent 接口拒绝超过最大长度的问题
def test_agent_chat_request_rejects_too_long_message() -> None:
    # Schema 最大允许 4000 个字符，这里故意创建 4001 个
    too_long_message = "a" * 4001

    # 超过 max_length 后应该抛出验证异常
    with pytest.raises(
        ValidationError,
    ):
        AgentChatRequest(
            message=too_long_message,
        )


# 验证 Agent 接口拒绝没有声明的额外字段
def test_agent_chat_request_rejects_extra_fields() -> None:
    # 模拟客户端试图伪造 role 字段
    invalid_request = {
        "message": "查询工单 ticket-001",
        "role": "tool",
    }

    # extra="forbid" 应该拒绝 role 字段
    with pytest.raises(
        ValidationError,
    ):
        AgentChatRequest.model_validate(
            invalid_request,
        )


# 验证 Agent 接口响应可以保存最终回答
def test_agent_chat_response_accepts_answer() -> None:
    # 模拟 Agent 已经生成最终回答
    response = AgentChatResponse(
        answer="工单 ticket-001 当前正在处理中。",
    )

    # 响应模型应该保存完整回答
    assert response.answer == (
        "工单 ticket-001 当前正在处理中。"
    )

    # 没有传入来源时默认使用空列表。
    assert response.sources == []
