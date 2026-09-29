"""OpenAI 兼容 /v1/chat/completions 客户端（支持流式）。"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import settings


class LLMError(RuntimeError):
    pass


def _check_config() -> None:
    if not settings.llm_base_url or not settings.llm_model:
        raise LLMError("未配置模型接口：请在 .env 中填写 LLM_BASE_URL 与 LLM_MODEL")


def _payload(messages: list[dict], stream: bool) -> dict:
    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "stream": stream,
    }
    if settings.llm_max_tokens:
        body["max_tokens"] = settings.llm_max_tokens
    return body


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        h["Authorization"] = f"Bearer {settings.llm_api_key}"
    return h


async def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        body = (await resp.aread()).decode("utf-8", "replace")[:500]
        raise LLMError(f"模型接口返回 {resp.status_code}: {body}")


async def complete(messages: list[dict]) -> str:
    """非流式调用，返回完整文本。"""
    _check_config()
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        try:
            resp = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                headers=_headers(),
                json=_payload(messages, stream=False),
            )
        except httpx.HTTPError as e:
            raise LLMError(f"无法连接模型接口：{e}") from e
        await _raise_for_status(resp)
        data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"模型返回格式异常：{str(data)[:300]}") from e


async def stream(messages: list[dict]) -> AsyncIterator[str]:
    """流式调用，逐段产出文本增量。"""
    _check_config()
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        try:
            async with client.stream(
                "POST",
                f"{settings.llm_base_url}/chat/completions",
                headers=_headers(),
                json=_payload(messages, stream=True),
            ) as resp:
                await _raise_for_status(resp)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise LLMError(f"模型接口错误：{chunk['error']}")
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as e:
            raise LLMError(f"模型接口连接中断：{e}") from e
