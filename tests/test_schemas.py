# 导入 pytest，用于参数化测试和验证异常
import pytest

# 导入 Pydantic 数据验证异常
from pydantic import ValidationError

# 导入需要测试的注册请求模型
from ticketpilot.schemas import UserCreateRequest


# 验证合法注册数据能够通过检查
def test_user_create_request_accepts_valid_data() -> None:
    # 使用合法数据创建注册请求对象
    request = UserCreateRequest(
        # 提供合法邮箱
        email="agent@example.com",

        # 故意在显示名称两端添加空格
        display_name="  测试客服  ",

        # 密码两端的空格属于密码本身，不应被删除
        password="  secure-password  ",
    )

    # 显示名称应该删除两端空格
    assert request.display_name == "测试客服"

    # SecretStr 应该保留密码原始内容
    assert (
        request.password.get_secret_value()
        == "  secure-password  "
    )

    # 打印整个请求对象时不能出现明文密码
    assert (
        "secure-password"
        not in repr(request)
    )


# 使用四组非法注册数据重复运行测试
@pytest.mark.parametrize(
    # 每轮测试接收一份非法数据
    "invalid_data",

    # 定义不同的非法注册场景
    [
        # 邮箱格式错误
        {
            "email": "not-an-email",
            "display_name": "测试客服",
            "password": "secure-password",
        },

        # 显示名称清理空格后为空
        {
            "email": "agent@example.com",
            "display_name": "   ",
            "password": "secure-password",
        },

        # 密码少于 8 个字符
        {
            "email": "agent@example.com",
            "display_name": "测试客服",
            "password": "short",
        },

        # 客户端不允许自行指定管理员角色
        {
            "email": "agent@example.com",
            "display_name": "测试客服",
            "password": "secure-password",
            "role": "admin",
        },
    ],
)
# 验证非法注册数据会被拒绝
def test_user_create_request_rejects_invalid_data(
    # 接收当前测试使用的数据字典
    invalid_data: dict[str, str],
) -> None:
    # 声明 Pydantic 必须抛出验证异常
    with pytest.raises(
        ValidationError
    ):
        # 使用当前非法数据创建请求模型
        UserCreateRequest(
            **invalid_data
        )