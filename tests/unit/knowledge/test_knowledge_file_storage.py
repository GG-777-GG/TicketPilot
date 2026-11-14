"""原始文件存储测试：仅使用 pytest 临时目录，不碰用户上传文件。"""

from pathlib import Path
from uuid import UUID

import pytest

from ticketpilot.knowledge.persistence import file_storage
from ticketpilot.knowledge.persistence.file_storage import (
    KnowledgeFileStorageError,
    LocalKnowledgeFileStorage,
)


def test_save_creates_directory_and_preserves_bytes(tmp_path: Path) -> None:
    directory = tmp_path / "nested" / "uploads"
    storage = LocalKnowledgeFileStorage(directory=directory)
    assert not directory.exists()  # 构造对象不能提前创建目录。
    content = b"%PDF-1.7\x00\xff\r\n"
    key = storage.save(content=content)
    assert Path(key).name == key
    assert key.endswith(".bin")
    assert UUID(key[:-4]).hex == key[:-4]
    assert (directory / key).read_bytes() == content


def test_read_returns_saved_bytes(tmp_path: Path) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    content = b"%PDF-1.7\x00\xff\r\n"
    key = storage.save(content=content)
    assert storage.read(storage_key=key) == content


@pytest.mark.parametrize("key", [
    "", "../secret.txt", r"..\secret.txt", "contract.pdf",
    "a" * 31 + ".bin", "a" * 33 + ".bin", "A" * 32 + ".bin",
    "a" * 32 + "xbin", "a" * 32 + ".bin\n",
    "a" * 32 + ".bin:stream", "/" + "a" * 32 + ".bin",
    "C:\\uploads\\" + "a" * 32 + ".bin",
])
def test_read_rejects_invalid_key_before_io(tmp_path: Path, monkeypatch, key: str) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)

    def unexpected_read(path):
        pytest.fail("非法标识不应触发文件读取")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    with pytest.raises(KnowledgeFileStorageError, match="标识不合法"):
        storage.read(storage_key=key)


def test_read_missing_file_is_not_empty_success(tmp_path: Path) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    with pytest.raises(KnowledgeFileStorageError, match="读取原始文件失败") as caught:
        storage.read(storage_key="a" * 32 + ".bin")
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_read_permission_failure_is_wrapped(tmp_path: Path, monkeypatch) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    error = PermissionError("simulated permission denial")

    def denied_read(path):
        raise error

    monkeypatch.setattr(Path, "read_bytes", denied_read)
    with pytest.raises(KnowledgeFileStorageError) as caught:
        storage.read(storage_key="a" * 32 + ".bin")
    assert caught.value.__cause__ is error


def test_read_checks_resolved_directory(tmp_path: Path, monkeypatch) -> None:
    # 模拟符号链接解析结果；Windows 上不依赖创建链接的管理员权限。
    directory = tmp_path / "uploads"
    storage = LocalKnowledgeFileStorage(directory=directory)
    outside = tmp_path / "secret.txt"
    monkeypatch.setattr(Path, "resolve", lambda path: outside)

    def unexpected_read(path):
        pytest.fail("目录外文件不应被读取")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    with pytest.raises(KnowledgeFileStorageError, match="超出存储目录"):
        storage.read(storage_key="a" * 32 + ".bin")


def test_delete_only_removes_selected_file(tmp_path: Path) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    selected = storage.save(content=b"remove")
    other = storage.save(content=b"keep")
    assert storage.delete(storage_key=selected) is None
    assert not (tmp_path / selected).exists()
    assert storage.read(storage_key=other) == b"keep"
    # 重复清理幂等，且目录本身保留。
    storage.delete(storage_key=selected)
    assert tmp_path.is_dir()


def test_delete_missing_file_does_not_create_directory(tmp_path: Path) -> None:
    directory = tmp_path / "missing"
    storage = LocalKnowledgeFileStorage(directory=directory)
    storage.delete(storage_key="a" * 32 + ".bin")
    assert not directory.exists()


@pytest.mark.parametrize("key", [
    "", "../secret.txt", r"..\secret.txt", "contract.pdf",
    "a" * 31 + ".bin", "a" * 33 + ".bin", "A" * 32 + ".bin",
    "a" * 32 + "xbin", "a" * 32 + ".bin\n",
    "a" * 32 + ".bin:stream", "/" + "a" * 32 + ".bin",
    "C:\\uploads\\" + "a" * 32 + ".bin",
])
def test_delete_rejects_invalid_key_before_io(tmp_path: Path, monkeypatch, key: str) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)

    def unexpected_unlink(path, **kwargs):
        pytest.fail("非法标识不应触发删除")

    monkeypatch.setattr(Path, "unlink", unexpected_unlink)
    with pytest.raises(KnowledgeFileStorageError, match="标识不合法"):
        storage.delete(storage_key=key)


def test_delete_rejects_resolved_outside_path(tmp_path: Path, monkeypatch) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path / "uploads")
    # 模拟路径解析到存储目录外，避免 Windows 创建链接的权限要求。
    monkeypatch.setattr(Path, "resolve", lambda path: tmp_path / "secret.txt")

    def unexpected_unlink(path, **kwargs):
        pytest.fail("不能删除存储目录外文件")

    monkeypatch.setattr(Path, "unlink", unexpected_unlink)
    with pytest.raises(KnowledgeFileStorageError, match="超出存储目录"):
        storage.delete(storage_key="a" * 32 + ".bin")


def test_delete_permission_error_is_not_ignored(tmp_path: Path, monkeypatch) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    error = PermissionError("simulated locked file")

    def denied_unlink(path, **kwargs):
        raise error

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    with pytest.raises(KnowledgeFileStorageError, match="删除原始文件失败") as caught:
        storage.delete(storage_key="a" * 32 + ".bin")
    assert caught.value.__cause__ is error


def test_delete_does_not_remove_directory(tmp_path: Path) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    # 即使目录名称恰好符合文件标识规则，也不应递归删除。
    key = "a" * 32 + ".bin"
    directory = tmp_path / key
    directory.mkdir()
    child = directory / "keep.txt"
    child.write_bytes(b"keep")
    with pytest.raises(KnowledgeFileStorageError):
        storage.delete(storage_key=key)
    assert child.read_bytes() == b"keep"


def test_saves_use_different_keys(tmp_path: Path) -> None:
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    first = storage.save(content=b"first")
    second = storage.save(content=b"second")
    assert first != second
    assert (tmp_path / first).read_bytes() == b"first"
    assert (tmp_path / second).read_bytes() == b"second"


def test_empty_file_does_not_create_directory(tmp_path: Path) -> None:
    directory = tmp_path / "uploads"
    storage = LocalKnowledgeFileStorage(directory=directory)
    with pytest.raises(KnowledgeFileStorageError, match="不能保存空文件"):
        storage.save(content=b"")
    assert not directory.exists()


def test_collision_preserves_existing_file(tmp_path: Path, monkeypatch) -> None:
    # 固定 UUID，主动制造名称冲突，而不是等待随机碰撞。
    fixed_uuid = UUID(int=1)
    monkeypatch.setattr(file_storage, "uuid4", lambda: fixed_uuid)
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    key = storage.save(content=b"original")
    with pytest.raises(KnowledgeFileStorageError) as caught:
        storage.save(content=b"replacement")
    assert isinstance(caught.value.__cause__, FileExistsError)
    assert (tmp_path / key).read_bytes() == b"original"


def test_directory_failure_is_wrapped(tmp_path: Path) -> None:
    # 使用临时目录中的普通文件占据目录位置。
    directory = tmp_path / "not-a-directory"
    directory.write_bytes(b"keep")
    with pytest.raises(KnowledgeFileStorageError) as caught:
        LocalKnowledgeFileStorage(directory=directory).save(content=b"new")
    assert isinstance(caught.value.__cause__, OSError)
    assert directory.read_bytes() == b"keep"


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_partial_write_cleanup(
    tmp_path: Path,  # pytest 为本题分配的隔离目录。
    monkeypatch,  # 自动恢复测试期间替换的方法。
    cleanup_fails: bool,  # 同时验证清理成功和清理失败两个分支。
) -> None:
    original_open = Path.open
    write_error = OSError("simulated disk full")
    cleanup_error = PermissionError("simulated cleanup failure")

    class PartialWriter:
        def __init__(self, path: Path):
            self.file = original_open(path, "xb")

        def __enter__(self):
            return self

        def write(self, content: bytes):
            self.file.write(content[:2])
            self.file.flush()
            raise write_error

        def __exit__(self, *args):
            self.file.close()

    def failing_open(path, mode="r", *args, **kwargs):
        if path.parent == tmp_path and mode == "xb":
            return PartialWriter(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    if cleanup_fails:
        def failing_unlink(path, **kwargs):
            raise cleanup_error
        monkeypatch.setattr(Path, "unlink", failing_unlink)

    with pytest.raises(KnowledgeFileStorageError) as caught:
        LocalKnowledgeFileStorage(directory=tmp_path).save(content=b"abcdef")
    if cleanup_fails:
        assert "残留文件清理失败" in str(caught.value)
        assert caught.value.__cause__ is cleanup_error
        assert len(list(tmp_path.iterdir())) == 1
    else:
        assert caught.value.__cause__ is write_error
        assert list(tmp_path.iterdir()) == []
