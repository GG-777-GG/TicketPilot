"""测试不同用户角色可以检索哪些知识文档。"""

import pytest

from ticketpilot.identity.types import UserRole


# 这个测试负责验证三种已知角色对应的文档可见范围。
@pytest.mark.parametrize(
    ("role", "expected_visibilities"),
    [
        ("agent", ("internal",)),
        ("supervisor", ("internal", "restricted")),
        ("admin", ("internal", "restricted")),
    ],
)
def test_allowed_visibilities_for_role(
    role: UserRole,
    expected_visibilities: tuple[str, ...],
) -> None:
    # 在测试函数内部导入，让新业务模块尚未创建时也能完成测试收集。
    from ticketpilot.knowledge.domain.access import (
        allowed_visibilities_for_role,
    )

    assert allowed_visibilities_for_role(role) == expected_visibilities


# 这个测试负责拒绝未知角色，避免意外放宽知识库权限。
def test_unknown_role_does_not_get_document_access() -> None:
    from ticketpilot.knowledge.domain.access import (
        allowed_visibilities_for_role,
    )

    with pytest.raises(ValueError, match="未知用户角色"):
        allowed_visibilities_for_role("guest")
