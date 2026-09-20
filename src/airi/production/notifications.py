"""Phase 9 notification contract.

An alert and a notification are two different things with two different failure
modes. Creating an alert must never roll back because a chat platform was down, so
delivery is recorded separately and a failed delivery is a fact about a sink - not
about the alert (§32).

AIRI does not implement an enterprise IM platform. It defines the seam, ships a
disabled sink and a mock sink, and talks to DingTalk only when a real webhook has
been configured. Nothing is retried in the background: a retry is a second
explicit call, made by a human or an external scheduler (§34).
"""

import json
import urllib.error
import urllib.request
from typing import Protocol

from airi.production.models import MetricAlert, NotificationResult


class AlertNotificationSink(Protocol):
    """The only surface the business layer depends on to notify anyone."""

    name: str

    def notify(self, alert: MetricAlert) -> NotificationResult: ...


class DisabledNotificationSink:
    """Default. Nobody is notified, and the attempt is still recorded as such."""

    name = "disabled"

    def notify(self, alert: MetricAlert) -> NotificationResult:  # noqa: ARG002
        return NotificationResult(
            status="disabled",
            sink=self.name,
            detail="no notification sink is configured; the alert remains the record",
        )


class MockNotificationSink:
    """Explicit test/demo injection. Never selectable through configuration."""

    name = "mock"

    def __init__(self, *, fail: bool = False, failure_category: str = "provider_rejected"):
        self.fail = fail
        self.failure_category = failure_category

    def notify(self, alert: MetricAlert) -> NotificationResult:
        if self.fail:
            return NotificationResult(
                status="failed",
                sink=self.name,
                failure_category=self.failure_category,
                detail="the mock sink was instructed to fail",
            )
        return NotificationResult(
            status="delivered",
            sink=self.name,
            provider_message_id=f"mock-{alert.alert_id[:12]}",
            detail="delivered to the mock sink",
        )


class DingTalkNotificationSink:
    """A real sink, used only when an operator supplies a webhook.

    The webhook is configuration, never a literal. A failure returns a categorised
    result instead of raising, so an unreachable chat platform can never undo an
    alert (§32).
    """

    name = "dingtalk"

    def __init__(self, webhook: str, *, timeout_seconds: float = 5.0):
        if not webhook:
            raise ValueError("a DingTalk webhook must be configured")
        self.webhook = webhook
        self.timeout_seconds = timeout_seconds

    def notify(self, alert: MetricAlert) -> NotificationResult:
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"AIRI {alert.severity} alert",
                "text": (
                    f"### AIRI {alert.severity} alert: {alert.type}\n\n"
                    f"- deployment: {alert.deployment_id}\n"
                    f"- metric: {alert.metric_key}\n"
                    f"- occurrences: {alert.occurrences}\n"
                    f"- recommended: {alert.recommended_action}\n"
                ),
            },
        }
        request = urllib.request.Request(
            self.webhook,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return NotificationResult(
                status="failed",
                sink=self.name,
                failure_category="http_error",
                detail=f"the webhook answered with status {exc.code}",
            )
        except Exception:
            return NotificationResult(
                status="failed",
                sink=self.name,
                failure_category="transport_error",
                detail="the webhook could not be reached",
            )
        if payload.get("errcode") not in (0, None):
            return NotificationResult(
                status="failed",
                sink=self.name,
                failure_category="provider_rejected",
                detail="the platform rejected the message",
            )
        return NotificationResult(
            status="delivered",
            sink=self.name,
            provider_message_id=str(payload.get("task_id") or "") or None,
            detail="delivered to DingTalk",
        )
