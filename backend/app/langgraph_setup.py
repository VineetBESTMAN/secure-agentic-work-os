from app.core.config import get_settings
from app.services.langgraph_workflows import langgraph_workflow_service


def main() -> None:
    if get_settings().workflow_engine == "langgraph":
        langgraph_workflow_service.setup_checkpoint_storage()


if __name__ == "__main__":
    main()
