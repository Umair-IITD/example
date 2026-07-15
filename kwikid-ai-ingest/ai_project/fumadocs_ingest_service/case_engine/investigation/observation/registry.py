"""
case_engine/investigation/observation/registry.py

Sprint 2.44: TemplateRegistry — thread-safe registry for observation templates.

Features:
  register()      — register a template by template_id (replace allowed)
  unregister()    — remove a template (raises TemplateNotFoundError)
  get()           — fetch by template_id (raises TemplateNotFoundError)
  all_templates() — sorted by (priority, template_id)
  resolve()       — priority-ordered first match; guaranteed fallback
  fallback        — the registered fallback template (or None)

resolve() never returns None — if no specific template matches, the
fallback template is returned; if no fallback is registered either,
ObservationConfigurationError is raised.

Dependency direction:
  registry.py → observation/contracts.py (ObservationTemplate)
  registry.py → observation/exceptions.py
  registry.py → observation/templates.py (build_default_registry only)
  registry.py → stdlib (threading)
"""
from __future__ import annotations

import threading

from case_engine.investigation.observation.contracts import ObservationTemplate
from case_engine.investigation.observation.exceptions import (
    ObservationConfigurationError,
    TemplateNotFoundError,
)


class TemplateRegistry:
    """
    Thread-safe registry mapping template_id → ObservationTemplate.

    Templates are matched in ascending priority order
    (priority=10 is tried before priority=9999).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._templates: dict[str, ObservationTemplate] = {}

    def register(self, template: ObservationTemplate) -> None:
        """Register a template. Overwrites any existing template with the same id."""
        with self._lock:
            self._templates[template.template_id] = template

    def unregister(self, template_id: str) -> None:
        """
        Remove a template by template_id.

        Raises TemplateNotFoundError if template_id is not registered.
        """
        with self._lock:
            if template_id not in self._templates:
                raise TemplateNotFoundError(template_id)
            del self._templates[template_id]

    def get(self, template_id: str) -> ObservationTemplate:
        """
        Return the registered template for template_id.

        Raises TemplateNotFoundError if not found.
        """
        with self._lock:
            template = self._templates.get(template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return template

    def is_registered(self, template_id: str) -> bool:
        with self._lock:
            return template_id in self._templates

    def all_templates(self) -> list[ObservationTemplate]:
        """Return all templates sorted by (priority, template_id)."""
        with self._lock:
            templates = list(self._templates.values())
        return sorted(templates, key=lambda t: (t.priority, t.template_id))

    @property
    def template_count(self) -> int:
        with self._lock:
            return len(self._templates)

    @property
    def fallback(self) -> ObservationTemplate | None:
        """Return the first registered fallback template, or None."""
        for template in self.all_templates():
            if template.is_fallback:
                return template
        return None

    def resolve(self, category: str, topic: str) -> ObservationTemplate:
        """
        Return the highest-priority template matching (category, topic).

        Per-template matching errors are swallowed (a broken template must
        never take down resolution). Falls back to the fallback template.

        Raises ObservationConfigurationError if nothing matches and no
        fallback is registered.
        """
        fallback: ObservationTemplate | None = None
        for template in self.all_templates():
            if template.is_fallback:
                if fallback is None:
                    fallback = template
                continue
            try:
                if template.matches(category, topic):
                    return template
            except Exception:  # noqa: BLE001 — a broken template must not block resolution
                continue
        if fallback is not None:
            return fallback
        raise ObservationConfigurationError(
            "TemplateRegistry has no matching template and no fallback. "
            "Register a fallback or use build_default_registry()."
        )

    def reset(self) -> None:
        """Clear all registrations. For test isolation only."""
        with self._lock:
            self._templates.clear()


def build_default_registry() -> TemplateRegistry:
    """Build a TemplateRegistry pre-populated with all 7 standard templates."""
    from case_engine.investigation.observation.templates import (
        ApiObservationTemplate,
        FallbackObservationTemplate,
        OcrObservationTemplate,
        OtpObservationTemplate,
        PortalObservationTemplate,
        SessionObservationTemplate,
        VkycObservationTemplate,
    )

    registry = TemplateRegistry()
    for template_cls in [
        OtpObservationTemplate,
        VkycObservationTemplate,
        OcrObservationTemplate,
        ApiObservationTemplate,
        PortalObservationTemplate,
        SessionObservationTemplate,
        FallbackObservationTemplate,
    ]:
        registry.register(template_cls())
    return registry
