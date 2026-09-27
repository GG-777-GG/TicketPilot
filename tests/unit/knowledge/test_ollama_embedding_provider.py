"""
测试 Ollama Embedding Provider。

本阶段验证：
- 单批文本能够生成向量。
- 调用 Ollama 时参数正确。
- 多段文本能够按照 batch_size 分批。
- 多批结果保持原来的文本顺序。
- 无效输入会在调用 Ollama 前被拒绝。

测试只使用 Fake Client，
不会连接电脑上真正的 Ollama 服务。
"""

# SimpleNamespace 用来模拟包含 embeddings 字段的响应对象。
from types import SimpleNamespace

# MagicMock 用来模拟 Ollama Client。
from unittest.mock import MagicMock

# pytest 提供参数化和异常断言。
import pytest

# 这两个异常分别模拟连接失败和读取超时。
from httpx import (
    ConnectError,
    ReadTimeout,
)

# Client 用作 MagicMock 的接口规范。
from ollama import (
    Client,
    RequestError as OllamaRequestError,
    ResponseError,
)

# 导入数据库规定的向量维度。
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入输入验证异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProviderError,
    InvalidEmbeddingInputError,
    InvalidEmbeddingResponseError,
)
# 导入需要测试的 Ollama Provider。
from ticketpilot.knowledge.embeddings.ollama import (
    OllamaEmbeddingProvider,
)


# 这个函数负责创建指定数值的 1024 维测试向量。
def build_test_vector(
    value: float = 0.1,
) -> list[float]:
    return (
        [value]
        * EMBEDDING_DIMENSIONS
    )


# 这个测试负责验证单批文本能够正确生成向量。
def test_embed_texts_returns_embedding_batch() -> None:
    # 创建假的 Ollama Client。
    fake_client = MagicMock(
        spec=Client
    )

    # 指定调用 embed() 时返回一个测试向量。
    fake_client.embed.return_value = (
        SimpleNamespace(
            embeddings=[
                build_test_vector()
            ]
        )
    )

    # 创建使用 Fake Client 的 Provider。
    provider = OllamaEmbeddingProvider(
        client=fake_client,
        model="test-embedding-model",
        batch_size=16,
    )

    # 为一段文字生成向量。
    result = provider.embed_texts(
        [
            "用户可以通过邮箱重置密码。"
        ]
    )

    # Provider 应返回配置中的模型名称。
    assert (
        result.model_name
        == "test-embedding-model"
    )

    # 向量维度必须与数据库一致。
    assert (
        result.dimensions
        == EMBEDDING_DIMENSIONS
    )

    # 一段输入文本对应一个向量。
    assert result.count == 1

    # 每个向量必须正好包含 1024 个数值。
    assert (
        len(result.vectors[0])
        == EMBEDDING_DIMENSIONS
    )

    # 验证传给 Ollama 的参数。
    fake_client.embed.assert_called_once_with(
        model="test-embedding-model",
        input=[
            "用户可以通过邮箱重置密码。"
        ],
        truncate=False,
        dimensions=EMBEDDING_DIMENSIONS,
    )


# 这个测试负责验证多段文本会被分批并保持原有顺序。
def test_embed_texts_batches_inputs_in_order() -> None:
    fake_client = MagicMock(
        spec=Client
    )

    # 第一次调用返回前两个向量，
    # 第二次调用返回最后一个向量。
    fake_client.embed.side_effect = [
        SimpleNamespace(
            embeddings=[
                build_test_vector(0.1),
                build_test_vector(0.2),
            ]
        ),
        SimpleNamespace(
            embeddings=[
                build_test_vector(0.3),
            ]
        ),
    ]

    provider = OllamaEmbeddingProvider(
        client=fake_client,
        batch_size=2,
    )

    result = provider.embed_texts(
        [
            "第一段",
            "第二段",
            "第三段",
        ]
    )

    # 3 段文本、每批最多 2 段，应该调用两次。
    assert fake_client.embed.call_count == 2

    # 最终仍然应该拥有三个向量。
    assert result.count == 3

    # 每个向量的第一个数值可以证明顺序没有变化。
    assert [
        vector[0]
        for vector in result.vectors
    ] == [
        0.1,
        0.2,
        0.3,
    ]

    # 检查每次调用实际收到的文本。
    assert [
        call.kwargs["input"]
        for call in fake_client.embed.call_args_list
    ] == [
        [
            "第一段",
            "第二段",
        ],
        [
            "第三段",
        ],
    ]


@pytest.mark.parametrize(
    "invalid_texts",
    [
        # 整个列表为空。
        [],

        # 直接传入单个字符串。
        "这不是文本列表",

        # 文本是空字符串。
        [""],

        # 文本只有空白字符。
        ["   "],

        # 集合中包含非字符串数据。
        [
            "正常文本",
            123,
        ],
    ],
)
# 这个测试负责验证无效文本会在调用 Ollama 前被拒绝。
def test_embed_texts_rejects_invalid_input(
    invalid_texts,
) -> None:
    fake_client = MagicMock(
        spec=Client
    )
    provider = OllamaEmbeddingProvider(
        client=fake_client
    )

    with pytest.raises(
        InvalidEmbeddingInputError
    ):
        provider.embed_texts(
            invalid_texts
        )

    # 输入不合法时绝对不能调用外部模型。
    fake_client.embed.assert_not_called()

@pytest.mark.parametrize(
    (
        "returned_vectors",
        "expected_message",
    ),
    [
        # 输入一段文本，却没有返回向量。
        (
            [],
            "向量数量",
        ),

        # 输入一段文本，却返回两个向量。
        (
            [
                build_test_vector(0.1),
                build_test_vector(0.2),
            ],
            "向量数量",
        ),

        # 向量少于 1024 维。
        (
            [
                [0.1]
                * (
                    EMBEDDING_DIMENSIONS
                    - 1
                )
            ],
            "向量维度",
        ),

        # 向量多于 1024 维。
        (
            [
                [0.1]
                * (
                    EMBEDDING_DIMENSIONS
                    + 1
                )
            ],
            "向量维度",
        ),

        # 向量中包含不能转换成数字的内容。
        (
            [
                [
                    "不是数字",
                    *(
                        [0.1]
                        * (
                            EMBEDDING_DIMENSIONS
                            - 1
                        )
                    ),
                ]
            ],
            "非法数值",
        ),

        # 向量中包含 NaN。
        (
            [
                [
                    float("nan"),
                    *(
                        [0.1]
                        * (
                            EMBEDDING_DIMENSIONS
                            - 1
                        )
                    ),
                ]
            ],
            "非有限数值",
        ),

        # 向量中包含正无穷。
        (
            [
                [
                    float("inf"),
                    *(
                        [0.1]
                        * (
                            EMBEDDING_DIMENSIONS
                            - 1
                        )
                    ),
                ]
            ],
            "非有限数值",
        ),

        # 向量中包含负无穷。
        (
            [
                [
                    float("-inf"),
                    *(
                        [0.1]
                        * (
                            EMBEDDING_DIMENSIONS
                            - 1
                        )
                    ),
                ]
            ],
            "非有限数值",
        ),

        # 全零向量不能参与有效检索。
        (
            [
                [0.0]
                * EMBEDDING_DIMENSIONS
            ],
            "不能全部为 0",
        ),
    ],
)
# 这个测试负责验证模型返回的错误向量会被拒绝。
def test_embed_texts_rejects_invalid_vectors(
    returned_vectors,
    expected_message: str,
) -> None:
    fake_client = MagicMock(
        spec=Client
    )
    fake_client.embed.return_value = (
        SimpleNamespace(
            embeddings=returned_vectors
        )
    )

    provider = OllamaEmbeddingProvider(
        client=fake_client
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match=expected_message,
    ):
        provider.embed_texts(
            ["测试文本"]
        )

@pytest.mark.parametrize(
    "service_error",
    [
        # 无法连接到 Ollama。
        ConnectError(
            "连接 Ollama 失败"
        ),

        # Ollama 响应超时。
        ReadTimeout(
            "等待 Ollama 超时"
        ),

        # Ollama SDK 请求异常。
        OllamaRequestError(
            "Ollama 请求错误"
        ),

        # Ollama 返回 HTTP 服务错误。
        ResponseError(
            "Ollama 服务错误",
            status_code=500,
        ),

        # 操作系统层面的连接异常。
        ConnectionError(
            "本地连接失败"
        ),
    ],
)
# 这个测试负责验证外部服务异常会转换成统一的项目异常。
def test_embed_texts_wraps_service_errors(
    service_error: Exception,
) -> None:
    fake_client = MagicMock(
        spec=Client
    )

    # 调用 embed() 时抛出当前参数提供的异常。
    fake_client.embed.side_effect = (
        service_error
    )

    provider = OllamaEmbeddingProvider(
        client=fake_client
    )

    with pytest.raises(
        EmbeddingProviderError,
        match="Ollama Embedding 服务调用失败",
    ) as exception_info:
        provider.embed_texts(
            ["测试文本"]
        )

    # 验证项目异常仍然保留真正的底层原因。
    assert (
        exception_info.value.__cause__
        is service_error
    )

# 这个测试负责验证响应缺少 embeddings 字段时会报错。
def test_embed_texts_rejects_missing_embeddings() -> None:
    fake_client = MagicMock(
        spec=Client
    )

    fake_client.embed.return_value = (
        SimpleNamespace()
    )

    provider = OllamaEmbeddingProvider(
        client=fake_client
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="缺少 embeddings 字段",
    ):
        provider.embed_texts(
            ["测试文本"]
        )