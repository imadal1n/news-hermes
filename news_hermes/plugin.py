from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeAlias

from .json_types import JsonValue
from .schemas import TOOL_SCHEMAS
from .tools import (
    news_bookmark,
    news_clear,
    news_dismiss,
    news_list,
    news_pull,
    news_source_add,
    news_source_list,
    news_source_remove,
)

ToolHandler: TypeAlias = Callable[..., str]
ToolSchema: TypeAlias = dict[str, JsonValue]


class ToolRegistrar(Protocol):
    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: ToolSchema,
        handler: ToolHandler,
    ) -> None: ...


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "news_list": news_list,
    "news_bookmark": news_bookmark,
    "news_dismiss": news_dismiss,
    "news_pull": news_pull,
    "news_clear": news_clear,
    "news_source_add": news_source_add,
    "news_source_remove": news_source_remove,
    "news_source_list": news_source_list,
}


def register(ctx: ToolRegistrar) -> None:
    for name, handler in TOOL_HANDLERS.items():
        ctx.register_tool(
            name=name,
            toolset="news-hermes",
            schema=TOOL_SCHEMAS[name],
            handler=handler,
        )
