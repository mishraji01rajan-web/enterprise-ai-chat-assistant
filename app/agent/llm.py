"""LLM provider abstraction.

Supports Anthropic, OpenAI, and Google Gemini via LangChain chat model
wrappers, selected purely by configuration (`LLM_PROVIDER` env var) — no
code changes needed to switch providers. An "offline" provider is also
available: a small deterministic stand-in used for local development,
CI, and the evaluation suite so the whole system (routing, SQL guard, tool
permissions, citation formatting) can be exercised and verified without any
API key or network access. It is never used unless explicitly configured.
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage

from app.config import settings


class OfflineChatModel(GenericFakeChatModel):
    """Deterministic offline stand-in for a real chat model.

    Rather than returning canned nonsense, it extracts the structured
    "system context" the agent nodes embed in the prompt (retrieved
    documents, SQL results, tool results) and produces a plain-language
    answer directly from that data. This keeps offline mode genuinely
    useful for tests/demo instead of just a placeholder.
    """

    messages: Any = iter([])

    def _call_impl(self, prompt_text: str) -> str:
        return _offline_synthesize(prompt_text)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: D401
        last_human = ""
        for m in reversed(messages):
            if m.type in ("human", "system"):
                last_human = m.content if isinstance(m.content, str) else str(m.content)
                break
        text = self._call_impl(last_human)
        from langchain_core.outputs import ChatGeneration, ChatResult

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _offline_synthesize(prompt_text: str) -> str:
    """Very small templated synthesizer used only in offline mode.

    Looks for the structured blocks the agent's synthesis prompt always
    includes (retrieved_documents / sql_results / tool_result) and turns
    them into a plain sentence. This is intentionally simple: offline mode
    exists for deterministic testing, not to imitate a real LLM.
    """
    lines: list[str] = []

    doc_matches = re.findall(r'doc_id="([^"]+)" title="([^"]+)">\s*(.*?)\s*</document>', prompt_text, re.DOTALL)
    if doc_matches:
        lines.append("Based on the retrieved policy/knowledge documents:")
        for doc_id, title, text in doc_matches[:3]:
            snippet = " ".join(text.split())[:280]
            lines.append(f"- ({doc_id} - {title}): {snippet}")
    elif "<retrieved_documents>" in prompt_text and "no relevant documents found" in prompt_text:
        lines.append(
            "I could not find any information about this in the knowledge base, "
            "so I don't want to guess. Please rephrase or contact the relevant team."
        )

    sql_match = re.search(r"<sql_results>(.*?)</sql_results>", prompt_text, re.DOTALL)
    if sql_match:
        content = sql_match.group(1).strip()
        if content and content != "[]":
            lines.append(f"From the database: {content}")
        else:
            lines.append("The database did not return any matching records for this query.")

    tool_match = re.search(r"<tool_result>(.*?)</tool_result>", prompt_text, re.DOTALL)
    if tool_match:
        content = tool_match.group(1).strip()
        if '"pending_approval": true' in content.replace(" ", "").lower():
            summary_match = re.search(r'"summary":\s*"([^"]*)"', content)
            approval_match = re.search(r'"approval_id":\s*"([^"]*)"', content)
            summary = summary_match.group(1) if summary_match else "this action"
            approval_id = approval_match.group(1) if approval_match else "unknown"
            lines.append(
                f"I've prepared the following action but it requires your confirmation before "
                f"it takes effect: {summary}. (approval id: {approval_id}) "
                f"Please approve or reject it before I proceed."
            )
        else:
            lines.append(f"Tool result: {content}")

    tool_error_match = re.search(r"<tool_error>(.*?)</tool_error>", prompt_text, re.DOTALL)
    if tool_error_match:
        lines.append(f"I couldn't complete that action: {tool_error_match.group(1).strip()}")

    if not lines:
        lines.append(
            "I don't have enough information to answer that confidently based on the "
            "available knowledge base and data. Could you clarify your question?"
        )

    return "\n".join(lines)


def get_chat_model() -> BaseChatModel:
    provider = settings.llm_provider

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.llm_model or "claude-sonnet-5",
            api_key=settings.anthropic_api_key,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model or "gpt-4o-mini",
            api_key=settings.openai_api_key,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.llm_model or "gemini-2.0-flash",
            google_api_key=settings.google_api_key,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    # offline / default fallback
    return OfflineChatModel(messages=iter([]))


def is_offline() -> bool:
    return settings.llm_provider == "offline"
