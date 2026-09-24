from app.integrations.llm_api import OpenAICompatibleClient


def make_client(base_url: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_key="test", base_url=base_url, model="test")


def test_chat_url_accepts_openai_style_base_url() -> None:
    client = make_client("https://workspace.example.com/compatible-mode/v1")

    assert client.chat_completions_url() == (
        "https://workspace.example.com/compatible-mode/v1/chat/completions"
    )


def test_chat_url_accepts_provider_root_and_full_endpoint() -> None:
    root_client = make_client("https://api.example.com")
    endpoint_client = make_client("https://api.example.com/v1/chat/completions")

    assert root_client.chat_completions_url() == "https://api.example.com/v1/chat/completions"
    assert endpoint_client.chat_completions_url() == "https://api.example.com/v1/chat/completions"
