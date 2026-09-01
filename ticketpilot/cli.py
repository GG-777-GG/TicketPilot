from uuid import uuid4

from ticketpilot.models import (
    ALLOWED_CHANNELS,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    PROJECT_NAME,
    Ticket,
)
from ticketpilot.storage import load_tickets, save_tickets


# 打印系统标题
def print_header() -> None:
    print("=" * 40)
    print(f"{PROJECT_NAME} - 命令行工单系统")
    print("=" * 40)


# 打印系统主菜单
def print_menu() -> None:
    print()
    print("1. 创建工单")
    print("2. 查看全部工单")
    print("3. 查看工单数量")
    print("4. 按编号查看工单")
    print("5. 修改工单状态")
    print("6. 删除工单")
    print("0. 退出系统")


# 获取必填文本，输入为空时要求用户重新输入
def ask_required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()

        if value:
            return value

        print("输入不能为空，请重新输入。")


# 获取限定选项，并将输入统一转换为小写
def ask_choice(
    prompt: str,
    allowed_values: set[str],
    error_message: str,
) -> str:
    while True:
        value = input(prompt).strip().lower()

        if value in allowed_values:
            return value

        print(error_message)


# 生成一条不容易重复的 UUID 工单编号
def create_ticket_id() -> str:
    return str(uuid4())


# 收集用户输入并创建一个 Ticket 对象
def collect_ticket(ticket_id: str) -> Ticket:
    customer_name = ask_required("请输入客户姓名：")
    contact_email = ask_required("请输入联系邮箱：")
    channel = ask_choice(
        "请输入来源渠道（web/phone/email/chat）：",
        ALLOWED_CHANNELS,
        "来源渠道只能是 web、phone、email 或 chat，请重新输入。",
    )
    subject = ask_required("请输入问题标题：")
    description = ask_required("请输入问题描述：")
    priority = ask_choice(
        "请输入优先级（low/medium/high）：",
        ALLOWED_PRIORITIES,
        "优先级只能是 low、medium 或 high，请重新输入。",
    )

    return Ticket(
        id=ticket_id,
        customer_name=customer_name,
        contact_email=contact_email,
        channel=channel,
        subject=subject,
        description=description,
        priority=priority,
    )


# 打印一个 Ticket 对象的完整信息
def print_ticket(ticket: Ticket) -> None:
    print("-" * 40)
    print(f"工单编号：{ticket.id}")
    print(f"客户姓名：{ticket.customer_name}")
    print(f"联系邮箱：{ticket.contact_email}")
    print(f"来源渠道：{ticket.channel}")
    print(f"问题标题：{ticket.subject}")
    print(f"问题描述：{ticket.description}")
    print(f"优先级：{ticket.priority}")
    print(f"状态：{ticket.status}")


# 打印列表中的全部工单
def list_tickets(tickets: list[Ticket]) -> None:
    if not tickets:
        print("\n暂无工单。")
        return

    print(f"\n共有 {len(tickets)} 条工单：")

    for ticket in tickets:
        print_ticket(ticket)


# 根据工单编号查找对象，找不到时返回 None
def find_ticket_by_id(
    tickets: list[Ticket],
    ticket_id: str,
) -> Ticket | None:
    for ticket in tickets:
        if ticket.id == ticket_id:
            return ticket

    return None


# 根据用户输入的编号查看一张工单
def show_ticket_by_id(tickets: list[Ticket]) -> None:
    ticket_id = ask_required("请输入工单编号：")
    ticket = find_ticket_by_id(tickets, ticket_id)

    if ticket is None:
        print(f"未找到工单 #{ticket_id}。")
        return

    print_ticket(ticket)


# 修改工单状态，保存失败时恢复原状态
def update_ticket_status(tickets: list[Ticket]) -> bool:
    ticket_id = ask_required("请输入要修改的工单编号：")
    ticket = find_ticket_by_id(tickets, ticket_id)

    if ticket is None:
        print(f"未找到工单 #{ticket_id}。")
        return False

    new_status = ask_choice(
        "请输入新状态（open/in_progress/resolved/closed）：",
        ALLOWED_STATUSES,
        "状态只能是 open、in_progress、resolved 或 closed。",
    )

    old_status = ticket.status
    ticket.status = new_status

    try:
        save_tickets(tickets)

    except RuntimeError as error:
        ticket.status = old_status
        print(f"状态修改失败，已恢复原状态：{error}")
        return False

    print(
        f"工单 #{ticket_id} 状态已从 "
        f"{old_status} 修改为 {new_status}。"
    )

    return True


# 删除指定工单，保存失败时将工单放回原位置
def delete_ticket(tickets: list[Ticket]) -> bool:
    ticket_id = ask_required("请输入要删除的工单编号：")
    ticket = find_ticket_by_id(tickets, ticket_id)

    if ticket is None:
        print(f"未找到工单 #{ticket_id}。")
        return False

    confirmation = ask_choice(
        f"确认删除工单 #{ticket_id}？（yes/no）：",
        {"yes", "no"},
        "只能输入 yes 或 no。",
    )

    if confirmation == "no":
        print("已取消删除。")
        return False

    ticket_index = tickets.index(ticket)
    deleted_ticket = tickets.pop(ticket_index)

    try:
        save_tickets(tickets)

    except RuntimeError as error:
        tickets.insert(ticket_index, deleted_ticket)
        print(f"删除失败，已恢复工单：{error}")
        return False

    print(f"工单 #{ticket_id} 已删除并保存。")
    return True


# 运行 TicketPilot 命令行交互流程
def run_cli() -> None:
    try:
        tickets = load_tickets()

    except RuntimeError as error:
        print(f"系统启动失败：{error}")
        return

    print_header()
    print(f"已加载 {len(tickets)} 条工单。")

    while True:
        print_menu()
        choice = input("请选择操作：").strip()

        if choice == "1":
            ticket_id = create_ticket_id()
            ticket = collect_ticket(ticket_id)
            tickets.append(ticket)

            try:
                save_tickets(tickets)

            except RuntimeError as error:
                tickets.pop()
                print(error)
                continue

            print(
                f"\n工单 #{ticket.id} 创建成功并已保存！"
            )

        elif choice == "2":
            list_tickets(tickets)

        elif choice == "3":
            print(f"\n当前共有 {len(tickets)} 条工单。")

        elif choice == "4":
            show_ticket_by_id(tickets)

        elif choice == "5":
            update_ticket_status(tickets)

        elif choice == "6":
            delete_ticket(tickets)

        elif choice == "0":
            print("感谢使用 TicketPilot，再见！")
            break

        else:
            print(
                "无效选项，请输入 0、1、2、3、4、5 或 6。"
            )