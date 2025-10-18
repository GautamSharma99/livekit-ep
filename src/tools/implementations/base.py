from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    """Abstract base class for all tools."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - interface only
        """Execute the tool with provided arguments."""
        raise NotImplementedError
