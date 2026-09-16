"""PyCore BasePlugin 注册：分派与写库。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pycore.plugins import BasePlugin, PluginRegistry, PluginResult

from src.services.tools import (
    COUNSELING_TOOL_NAMES,
    INTERVIEW_TOOL_NAMES,
    KNOWLEDGE_TOOL_NAMES,
    NUDGE_TOOL_NAMES,
    ORCHESTRATOR_DEFINITIONS,
    TOOL_DEFINITIONS,
    ToolExecutor,
    definitions_for,
)


class ExecutorPlugin(BasePlugin):
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None

    async def execute(self, **kwargs: Any) -> PluginResult:
        executor: ToolExecutor | None = getattr(self, "_executor", None)
        if executor is None:
            return self.fail("未绑定执行器")
        args = {key: value for key, value in kwargs.items() if not str(key).startswith("_")}
        observation = await executor.execute(self.name, args)
        return self.ok(observation)


class DispatchPlugin(BasePlugin):
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None

    async def execute(self, **kwargs: Any) -> PluginResult:
        runner: Callable[..., Awaitable[dict[str, Any]]] | None = getattr(self, "_runner", None)
        if runner is None:
            return self.fail("未绑定分派")
        args = {key: value for key, value in kwargs.items() if not str(key).startswith("_")}
        data = await runner(**args)
        return self.ok(data)


def _plugin_from_spec(spec: dict[str, Any]) -> ExecutorPlugin:
    function = spec.get("function") or {}
    return ExecutorPlugin(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        parameters=function.get("parameters") or {"type": "object", "properties": {}},
    )


def _bind_executor(registry: PluginRegistry, executor: ToolExecutor) -> PluginRegistry:
    for plugin in registry:
        plugin._executor = executor
    return registry


def build_tool_registry(executor: ToolExecutor, names: tuple[str, ...]) -> PluginRegistry:
    registry = PluginRegistry()
    for spec in definitions_for(names):
        registry.register(_plugin_from_spec(spec))
    return _bind_executor(registry, executor)


def build_nudge_registry(executor: ToolExecutor) -> PluginRegistry:
    return build_tool_registry(executor, NUDGE_TOOL_NAMES)


def build_interview_registry(executor: ToolExecutor) -> PluginRegistry:
    return build_tool_registry(executor, INTERVIEW_TOOL_NAMES)


def build_knowledge_registry(executor: ToolExecutor) -> PluginRegistry:
    return build_tool_registry(executor, KNOWLEDGE_TOOL_NAMES)


def build_counseling_registry(executor: ToolExecutor) -> PluginRegistry:
    return build_tool_registry(executor, COUNSELING_TOOL_NAMES)


def build_orchestrator_registry(
    executor: ToolExecutor,
    runners: dict[str, Callable[..., Awaitable[dict[str, Any]]]],
) -> PluginRegistry:
    registry = PluginRegistry()
    for spec in ORCHESTRATOR_DEFINITIONS:
        function = spec.get("function") or {}
        name = str(function.get("name") or "")
        if name == "get_snapshot":
            snapshot_spec = next(
                item
                for item in TOOL_DEFINITIONS
                if item.get("function", {}).get("name") == "get_snapshot"
            )
            registry.register(_plugin_from_spec(snapshot_spec))
            continue
        dispatcher = DispatchPlugin(
            name=name,
            description=str(function.get("description") or ""),
            parameters=function.get("parameters") or {"type": "object", "properties": {}},
        )
        object.__setattr__(dispatcher, "_runner", runners[name])
        registry.register(dispatcher)
    return _bind_executor(registry, executor)
