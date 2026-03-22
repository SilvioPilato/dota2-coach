"""LiteLLM call + model selection."""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()


class CoachError(Exception):
    pass


def get_coaching(system_prompt: str, user_message: str, model: str) -> str:
    """
    Call LiteLLM with the given model and messages. Returns the response text.
    model format: 'anthropic/claude-sonnet-4-6', 'openai/gpt-4o', 'ollama/llama3'
    Never hardcode a model name — always pass via parameter.
    """
    try:
        import litellm

        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            timeout=300,
        )
        msg = response.choices[0].message
        content = msg.content
        if not content:
            # Fallback for thinking models (e.g. Qwen3) that put output in reasoning_content
            content = getattr(msg, "reasoning_content", None) or ""
        return content
    except Exception as exc:
        provider = model.split("/")[0] if "/" in model else model
        raise CoachError(f"LLM call failed for provider '{provider}': {exc}") from exc


async def stream_llm(messages: list[dict], model: str):
    """
    Async generator yielding text chunks from LiteLLM streaming completion.

    Usage with FastAPI StreamingResponse:
        return StreamingResponse(stream_llm(msgs, model), media_type="text/event-stream")
    """
    try:
        import litellm

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=True,
            timeout=300,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None) or ""
            if text:
                yield text
    except Exception as exc:
        provider = model.split("/")[0] if "/" in model else model
        raise CoachError(f"LLM streaming failed for provider '{provider}': {exc}") from exc
