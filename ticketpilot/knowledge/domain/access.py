"""根据用户角色决定可以检索哪些知识文档。

业务流程：
取得已登录用户的角色
→ 找出该角色允许查看的文档类型
→ 将结果交给后续数据库查询作过滤
"""

# 导入项目已有的三种用户角色类型。
from ticketpilot.identity.types import UserRole


# 这个函数负责返回当前角色可查看的文档类型。
def allowed_visibilities_for_role(
    role: UserRole,
) -> tuple[str, ...]:
    if role == "agent":
        return ("internal",)

    if role in ("supervisor", "admin"):
        return ("internal", "restricted")

    # 未知角色不能默认获得权限。
    raise ValueError("未知用户角色")