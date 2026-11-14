"""负责保存和读取异步入库使用的原始文件。

这里仅处理文件存储，不执行 PDF 解析、OCR 或数据库事务。
"""
# Python 标准库中的正则表达式模块。
# 用于检查文件标识是否完全符合系统生成的格式。
import re

# Python 标准库中的路径类型。
from pathlib import Path

# Python 标准库提供的随机 UUID 生成函数。
# 用于生成内部文件名，避免用户上传的同名文件互相覆盖。
from uuid import uuid4


class KnowledgeFileStorageError(RuntimeError):
    """表示原始文件保存、读取等操作失败。"""


class LocalKnowledgeFileStorage:
    """把异步入库文件保存在本机指定目录。"""

    def __init__(
        self,
        *,
        # 由服务端配置提供的文件目录。
        # 不能直接使用用户请求中传入的目录。
        directory: Path,
    ) -> None:
        # 转换成绝对路径，并解析路径中的 .. 等部分。
        # 此操作只确定路径，不创建目录，也不保存文件。
        self._directory = directory.resolve()

    def save(
        self,
        *,
        # 上传文件的原始二进制内容。
        # 调用前，业务层需要完成文件大小等上传校验。
        content: bytes,
    ) -> str:
        """保存原始文件，返回内部标识，不返回服务器绝对路径。"""

        # 空文件没有处理价值，提前拒绝。
        if not content:
            raise KnowledgeFileStorageError(
                "不能保存空文件"
            )

        # .hex 将 UUID 转成不带连字符的字符串。
        # 文件名完全由系统生成，不使用用户上传的名称。
        storage_key = f"{uuid4().hex}.bin"

        # 只能保存到配置目录下的这个内部文件名。
        target_path = self._directory / storage_key

        # 记录本次是否成功创建了文件。
        # 写入失败时，只清理本次创建的文件。
        created = False

        try:
            # 目录不存在时创建。
            # parents=True：缺少上级目录时，一起创建。
            # exist_ok=True：目录已经存在时，不因此报错。
            self._directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            # x：只创建新文件，已存在就报错，绝不覆盖。
            # b：按二进制写入，适用于 PDF、DOCX 等文件。
            # with 结束时自动关闭文件，包括发生异常时。
            with target_path.open("xb") as file:
                created = True
                file.write(content)

        except OSError as error:
            # 磁盘空间不足等问题可能留下只写了一部分的文件。
            if created:
                try:
                    # 只删除本次创建的具体文件，不删除整个目录。
                    # 文件已经不存在时，不再报错。
                    target_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise KnowledgeFileStorageError(
                        "保存文件失败，且残留文件清理失败"
                    ) from cleanup_error

            # 保留底层原因，供后续排查磁盘或权限问题。
            raise KnowledgeFileStorageError(
                "保存原始文件失败"
            ) from error

        # 只有完整写入并关闭文件后，才返回内部标识。
        return storage_key

    def _resolve_path(
        self,
        *,
        # save() 返回的内部文件标识，不接受完整路径。
        storage_key: str,
    ) -> Path:
        """检查文件标识和目录范围，返回允许操作的路径。"""

        # 必须完全符合“32 位小写十六进制字符 + .bin”。
        if re.fullmatch(r"[0-9a-f]{32}\.bin", storage_key) is None:
            raise KnowledgeFileStorageError(
                "原始文件标识不合法"
            )

        # 拼接路径，并解析符号链接等实际指向。
        target_path = (
            self._directory / storage_key
        ).resolve()

        # 解析后的文件必须直接位于指定存储目录下。
        if target_path.parent != self._directory:
            raise KnowledgeFileStorageError(
                "原始文件路径超出存储目录"
            )

        # 这里只返回路径，不读取、不删除，也不创建文件。
        return target_path

    def read(
        self,
        *,
        # 保存文件时返回的内部标识。
        # 例如：32 位十六进制字符加上 .bin。
        # 这里不接受原始文件名或完整路径。
        storage_key: str,
    ) -> bytes:
        """校验内部文件标识，并读取对应的原始文件。"""

        try:
            # 使用共用方法检查标识和路径。
            target_path = self._resolve_path(
                storage_key=storage_key,
            )

            # 路径检查通过后，才读取文件内容。
            return target_path.read_bytes()

        except OSError as error:
            # 路径解析或读取发生系统错误时，统一转换异常。
            raise KnowledgeFileStorageError(
                "读取原始文件失败"
            ) from error

    def delete(
        self,
        *,
        # save() 返回的内部文件标识。
        # 不接受用户上传的文件名或完整路径。
        storage_key: str,
    ) -> None:
        """删除指定原始文件；文件已经不存在时视为清理完成。"""

        try:
            # 与 read() 使用同一套标识和路径检查。
            target_path = self._resolve_path(
                storage_key=storage_key,
            )

            # 只删除指定文件；已经不存在时，视为清理完成。
            target_path.unlink(missing_ok=True)

        except OSError as error:
            # 路径解析或删除发生系统错误时，统一转换异常。
            raise KnowledgeFileStorageError(
                "删除原始文件失败"
            ) from error