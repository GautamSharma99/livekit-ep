from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool


class HangUpTool(BaseTool):
    """Tool to terminate the current call/session."""

    name = "hang_up"
    description = "Terminates the current call or session."

    def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        # TODO: Implement hang-up integration (e.g., SIP termination) here
        return {"status": "hung_up"}
