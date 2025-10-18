from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseService(ABC):
    """Abstract base class for services in the application."""

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - interface only
        """Execute the service's primary operation."""
        raise NotImplementedError
