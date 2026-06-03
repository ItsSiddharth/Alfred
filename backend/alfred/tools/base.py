"""
Tool bus base: AlfredTool ABC, ToolResult, ToolRegistry.
"""
from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Any
    error: str | None = None
    sources: list[str] = []


class AlfredTool(ABC):
    name: str = ""
    description: str = ""
    input_schema: type[BaseModel] | None = None

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.enabled: bool = True

    @abstractmethod
    async def execute(self, input_data: dict[str, Any]) -> ToolResult: ...

    def to_schema_dict(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
        }
        if self.input_schema is not None:
            schema["parameters"] = self.input_schema.model_json_schema()
        return schema


class ToolRegistry:
    _instance: "ToolRegistry | None" = None

    def __init__(self) -> None:
        self._tools: dict[str, AlfredTool] = {}

    @classmethod
    def get(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load_from_yaml(self, yaml_path: Path) -> None:
        if not yaml_path.exists():
            logger.warning("tools.yaml not found at %s", yaml_path)
            return
        with yaml_path.open() as fh:
            cfg = yaml.safe_load(fh) or {}
        for entry in cfg.get("tools", []):
            try:
                mod = importlib.import_module(entry["module"])
                cls_obj = getattr(mod, entry["class"])
                instance: AlfredTool = cls_obj(config=entry.get("config", {}))
                instance.enabled = entry.get("default_enabled", True)
                self._tools[instance.name] = instance
                logger.info("Loaded tool: %s (enabled=%s)", instance.name, instance.enabled)
            except Exception as exc:
                logger.error("Failed to load tool %s: %s", entry.get("name"), exc)

    def get_tool(self, name: str) -> AlfredTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[AlfredTool]:
        return list(self._tools.values())

    def enabled_tools(self) -> list[AlfredTool]:
        return [t for t in self._tools.values() if t.enabled]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        tool = self._tools.get(name)
        if tool is None:
            return False
        tool.enabled = enabled
        return True

    def to_schema_list(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        tools = self.enabled_tools() if only_enabled else self.list_tools()
        return [t.to_schema_dict() for t in tools]