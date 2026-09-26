from __future__ import annotations

import re
from typing import Any, Sequence

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from core.config import Settings, normalized_provider, require_llm_credentials


def build_llm(settings: Settings, temperature: float = 0.0):
    provider = normalized_provider(settings)
    require_llm_credentials(settings)

    if provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=settings.model_name,
            google_api_key=settings.google_api_key,
            temperature=temperature,
        )
    if provider == "openai":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.openai_api_key,
            temperature=temperature,
        )
    if provider == "anthropic":
        return ChatAnthropic(
            model=settings.model_name,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
        )
    if provider == "openrouter":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            temperature=temperature,
        )
    if provider == "ollama":
        return ChatOllama(
            model=settings.model_name,
            base_url=settings.ollama_base_url,
            temperature=temperature,
        )
    if provider == "custom":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.custom_llm_api_key or "unused",
            base_url=settings.custom_llm_base_url,
            temperature=temperature,
        )
    if provider == "mock":
        return MockToolCallingChatModel()
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")


class MockToolCallingChatModel(BaseChatModel):
    """Offline, deterministic stand-in for a tool-calling LLM (`LLM_PROVIDER=mock`).

    Turn 1: calls `lookup_paper` when the question quotes a title, else `semantic_search_papers`.
    Turn 2: falls back to semantic search if the exact lookup missed, otherwise answers
    from the top hit returned by the tool.
    It cannot grade answers, so structured output raises and the heuristic judge is used.
    """

    bound_tool_names: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "mock-tool-calling"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "MockToolCallingChatModel":
        names = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self.model_copy(update={"bound_tool_names": names})

    def with_structured_output(self, schema: Any, **kwargs: Any):
        raise NotImplementedError("The mock LLM cannot produce structured judgements.")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        question = next((str(item.content) for item in reversed(messages) if isinstance(item, HumanMessage)), "")
        quoted_title = re.search(r"'([^']+)'", question)
        lookup_missed = isinstance(last, ToolMessage) and str(last.content).startswith("No exact paper match")

        if isinstance(last, ToolMessage) and not (lookup_missed and "semantic_search_papers" in self.bound_tool_names):
            message = AIMessage(content=self._answer_from_tool_output(str(last.content)))
        elif not isinstance(last, ToolMessage) and quoted_title and "lookup_paper" in self.bound_tool_names:
            message = self._tool_call("lookup_paper", {"paper_id_or_title": quoted_title.group(1)}, len(messages))
        elif "semantic_search_papers" in self.bound_tool_names:
            message = self._tool_call("semantic_search_papers", {"query": question, "top_k": 3}, len(messages))
        else:
            message = AIMessage(content="This is a mock response from the scholarly corpus.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _tool_call(name: str, args: dict[str, Any], turn: int) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"mock_call_{turn}", "type": "tool_call"}])

    @staticmethod
    def _answer_from_tool_output(output: str) -> str:
        top_hit = output.split("\n\n")[0]
        fields = dict(line.split(": ", 1) for line in top_hit.splitlines() if ": " in line)
        if "title" not in fields:
            return "I could not find a supporting paper in the indexed corpus."
        return (
            f"[mock] Top match: '{fields['title']}' ({fields.get('paper_id', 'unknown id')}), "
            f"published {fields.get('Published', 'unknown')}, by {fields.get('Authors', 'unknown authors')}. "
            f"{fields.get('Summary', '')}"
        ).strip()
