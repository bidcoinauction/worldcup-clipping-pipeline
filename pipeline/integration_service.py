"""Optional integration adapter boundary.

External integrations (OORT, Airtable, Slack) are optional adapters around the
Clipper core. The core runtime, analysis, Moments, Stories, Render, and Export
behavior never depends on them. Adapters declare capabilities and provide
health checks; operations fail safely and never corrupt core runtime records.
"""

from __future__ import annotations

import os
from typing import Any

from .integration_models import AdapterHealth, IntegrationResult


class IntegrationAdapter:
    """Minimal adapter interface. Subclasses declare capabilities."""

    adapter_id: str = ""
    display_name: str = ""
    capabilities: tuple[str, ...] = ()
    _config_keys: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        return all(os.environ.get(key) for key in self._config_keys)

    def _provider_available(self) -> bool:
        return False

    def health_check(self) -> AdapterHealth:
        configured = self.is_configured()
        if not configured:
            return AdapterHealth(self.adapter_id, configured=False, available=False,
                                 status="NOT_CONFIGURED", message="adapter is not configured",
                                 capabilities=self.capabilities)
        if not self._provider_available():
            return AdapterHealth(self.adapter_id, configured=True, available=False,
                                 status="UNAVAILABLE", message="provider is not available",
                                 capabilities=self.capabilities)
        return AdapterHealth(self.adapter_id, configured=True, available=True,
                             status="READY", capabilities=self.capabilities)

    def upload_artifact(self, path: str, **kwargs: Any) -> IntegrationResult:
        return self._unavailable("upload_artifact")

    def publish_record(self, payload: dict[str, Any], **kwargs: Any) -> IntegrationResult:
        return self._unavailable("publish_record")

    def send_notification(self, message: str, **kwargs: Any) -> IntegrationResult:
        return self._unavailable("send_notification")

    def _unavailable(self, operation: str) -> IntegrationResult:
        return IntegrationResult(self.adapter_id, operation, ok=False, status="NOT_AVAILABLE",
                                 message="adapter is not configured")


class OORTAdapter(IntegrationAdapter):
    adapter_id = "oort"
    display_name = "OORT"
    capabilities = ("ARTIFACT_STORAGE",)
    _config_keys = ("OORT_API_KEY", "OORT_BUCKET")


class AirtableAdapter(IntegrationAdapter):
    adapter_id = "airtable"
    display_name = "Airtable"
    capabilities = ("RECORD_SYNC",)
    _config_keys = ("AIRTABLE_API_TOKEN", "AIRTABLE_BASE_ID")


class SlackAdapter(IntegrationAdapter):
    adapter_id = "slack"
    display_name = "Slack"
    capabilities = ("NOTIFICATION", "REVIEW_CARD")
    _config_keys = ("SLACK_WEBHOOK_URL",)


_ADAPTERS: dict[str, IntegrationAdapter] = {
    adapter.adapter_id: adapter for adapter in (OORTAdapter(), AirtableAdapter(), SlackAdapter())
}


def registered_integration_adapters() -> list[IntegrationAdapter]:
    return list(_ADAPTERS.values())


def get_integration_adapter(adapter_id: str) -> IntegrationAdapter:
    adapter = _ADAPTERS.get(adapter_id)
    if adapter is None:
        raise ValueError(f"unknown integration adapter: {adapter_id}")
    return adapter


def integration_health_report() -> list[dict[str, Any]]:
    return [adapter.health_check().to_dict() for adapter in _ADAPTERS.values()]