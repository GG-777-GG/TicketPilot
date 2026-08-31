PROJECT_NAME = "TicketPilot"


def build_welcome_message(learner_name: str) -> str:
    message = f"{learner_name}，欢迎来到 {PROJECT_NAME}！"
    return message


def main() -> None:
    learner_name = "GG"
    welcome_message = build_welcome_message(learner_name)
    print(welcome_message)


if __name__ == "__main__":
    main()