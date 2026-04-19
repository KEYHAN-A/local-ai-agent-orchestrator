# SPDX-License-Identifier: GPL-3.0-or-later
"""
Optional OpenTelemetry exporter (lazy-loaded behind ``LAO_OTEL_ENDPOINT`` /
``settings.otel.enabled``).

Zero cost when telemetry is disabled: :func:`span` returns a no-op context
manager that does not import OpenTelemetry at all. When enabled the first
``span`` call lazy-imports the SDK and registers a single global tracer
provider.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

log = logging.getLogger(__name__)


_TRACER = None
_INITIALIZED = False


def _settings_endpoint() -> Optional[str]:
    try:
        from local_ai_agent_orchestrator.settings import get_settings

        s = get_settings()
        if s.otel.enabled and s.otel.endpoint:
            return s.otel.endpoint
    except Exception:
        return None
    return None


def _resolve_endpoint() -> Optional[str]:
    env = os.getenv("LAO_OTEL_ENDPOINT")
    if env:
        return env
    return _settings_endpoint()


def _service_name() -> str:
    try:
        from local_ai_agent_orchestrator.settings import get_settings

        return get_settings().otel.service_name
    except Exception:
        return "lao"


def _ensure_tracer():
    global _TRACER, _INITIALIZED
    if _INITIALIZED:
        return _TRACER
    _INITIALIZED = True
    endpoint = _resolve_endpoint()
    if not endpoint:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as e:
        log.warning(f"[OTel] not installed; install opentelemetry-sdk to enable. ({e})")
        return None
    resource = Resource.create({"service.name": _service_name()})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("lao")
    log.info(f"[OTel] tracer initialized for endpoint={endpoint}")
    return _TRACER


@contextmanager
def span(name: str, **attributes) -> Iterator[None]:
    tracer = _ensure_tracer()
    if tracer is None:
        yield
        return
    with tracer.start_as_current_span(name) as sp:  # type: ignore[union-attr]
        for k, v in attributes.items():
            try:
                sp.set_attribute(k, v)
            except Exception:
                pass
        yield
