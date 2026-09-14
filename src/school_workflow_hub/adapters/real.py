"""Real-adapter shells: unavailable means not configured, never fake success."""

from __future__ import annotations

from school_workflow_hub.core.models import AdapterResult


class NotConfiguredAdapter:
    name = "unconfigured"

    def inspect_target(self, target_stable_id: str) -> AdapterResult:
        return AdapterResult("not_configured", reason=f"{self.name} has no verified configuration")

    def execute_authorized_action(self, action_id: str) -> AdapterResult:
        return AdapterResult("not_configured", reason=f"{self.name} has no verified executor")


class SchoologyBrowserAdapter(NotConfiguredAdapter):
    name = "schoology_browser"


class InfiniteCampusBrowserAdapter(NotConfiguredAdapter):
    name = "infinite_campus_browser"
