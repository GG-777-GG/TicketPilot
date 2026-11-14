"""负责验证并解析用户上传的知识文件。

业务流程：
接收文件名、Content-Type 和文件内容
→ 检查文件名是否合法
→ 检查文件是否为空
→ 检查文件大小
→ 调用解析器注册表
→ 返回统一的纯文本解析结果
"""

from pathlib import Path

from ticketpilot.knowledge.parsers import (
    KnowledgeFileParserRegistry,
    ParsedKnowledgeFile,
)


# 默认最多允许上传 5 MiB。
DEFAULT_MAX_FILE_SIZE_BYTES = (
    5 * 1024 * 1024
)


class KnowledgeFileValidationError(
    ValueError
):
    """表示上传文件没有通过安全检查。"""

class KnowledgeFileTooLargeError(
    KnowledgeFileValidationError
):
    """表示上传文件超过系统允许的大小。"""


class KnowledgeFileUploadService:
    """负责验证和解析知识文件。"""

    def __init__(
        self,
        *,
        parser_registry: (
            KnowledgeFileParserRegistry
            | None
        ) = None,
        max_file_size_bytes: int = (
            DEFAULT_MAX_FILE_SIZE_BYTES
        ),
    ) -> None:
        # 测试可以传入假的注册表；
        # 正式运行时使用默认注册表。
        self._parser_registry = (
            parser_registry
            or KnowledgeFileParserRegistry()
        )

        self._max_file_size_bytes = (
            max_file_size_bytes
        )

    # 这个属性负责公开当前文件大小限制。
    @property
    def max_file_size_bytes(
            self,
    ) -> int:
        return self._max_file_size_bytes

    def validate_upload(
        self,
        *,
        # 原始文件名，用于检查名称是否合法、扩展名是否支持。
        filename: str,

        # 上传请求声明的内容类型，传给解析器注册表。
        # 它不是文件真实格式的可靠证明。
        content_type: str | None,

        # 原始二进制内容，用于检查是否为空、是否超过大小限制。
        content: bytes,
    ) -> None:
        """检查上传信息，不解析文件，也不执行 OCR。"""

        # 复用已有的文件名检查。
        self._validate_filename(filename)

        # 复用已有的空文件和大小检查。
        self._validate_content(content)

        # 确认存在支持这个文件的解析器。
        # 这里只选择解析器，不调用它的 parse() 方法。
        # 不支持时，注册表会抛出已有的文件类型异常。
        self._parser_registry.get_parser(
            filename=filename,
            content_type=content_type,
        )

    # 这个函数负责验证并解析上传文件。
    def parse_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ParsedKnowledgeFile:
        # 同步解析和异步提交共用同一套上传检查。
        self.validate_upload(
            filename=filename,
            content_type=content_type,
            content=content,
        )

        return self._parser_registry.parse(
            filename=filename,
            content_type=content_type,
            content=content,
        )

    # 这个函数负责检查上传文件名。
    def _validate_filename(
        self,
        filename: str,
    ) -> None:
        if not filename.strip():
            raise KnowledgeFileValidationError(
                "上传文件必须包含文件名"
            )

        # 空字符不应该出现在正常文件名中。
        if "\x00" in filename:
            raise KnowledgeFileValidationError(
                "上传文件名不合法"
            )

        # 限制文件名长度，避免异常输入。
        if len(filename) > 255:
            raise KnowledgeFileValidationError(
                "上传文件名不能超过 255 个字符"
            )

        # Path(filename).name 只保留最后的文件名。
        # 如果结果不同，说明客户端传入了目录路径。
        if Path(filename).name != filename:
            raise KnowledgeFileValidationError(
                "上传文件名不能包含目录路径"
            )

    # 这个函数负责检查文件内容和大小。
    def _validate_content(
        self,
        content: bytes,
    ) -> None:
        if not content:
            raise KnowledgeFileValidationError(
                "上传文件不能为空"
            )

        if len(content) > self._max_file_size_bytes:
            raise KnowledgeFileTooLargeError(
                "上传文件超过允许的大小"
            )