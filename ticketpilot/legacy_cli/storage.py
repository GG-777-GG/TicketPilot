import json
from dataclasses import asdict
from pathlib import Path

from ticketpilot.legacy_cli.models import Ticket


# 文件位于 ticketpilot/legacy_cli/storage.py，项目根目录需要向上三级。
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = BASE_DIR / "data" / "tickets.json"


# 从 JSON 文件加载并转换全部工单对象
def load_tickets() -> list[Ticket]:
    if not DATA_FILE.exists():
        return []

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"读取工单数据失败：{error}"
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            "读取工单数据失败：JSON 顶层必须是列表。"
        )

    tickets: list[Ticket] = []

    try:
        for item in data:
            if not isinstance(item, dict):
                raise TypeError("每条工单必须是 JSON 对象")

            ticket = Ticket(**item)
            tickets.append(ticket)

    except TypeError as error:
        raise RuntimeError(
            f"读取工单数据失败：工单字段不正确，{error}"
        ) from error

    return tickets


# 将全部工单对象转换为字典并保存到 JSON 文件
def save_tickets(tickets: list[Ticket]) -> None:
    data: list[dict[str, str]] = []

    for ticket in tickets:
        ticket_data = asdict(ticket)
        data.append(ticket_data)

    try:
        DATA_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except OSError as error:
        raise RuntimeError(
            f"保存工单数据失败：{error}"
        ) from error
