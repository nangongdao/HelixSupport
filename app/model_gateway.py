"""Single governed entry for every model transport (ROADMAP H04 / T02).

Every model call — the triage decision, language detection and translation,
session summaries, operator copilot — must clear the *same* tenant policy
before it reaches the transport:

* the per-tenant daily turn budget (Phase 19.4);
* the ``allowed_models`` allow-list (Phase 19.4);
* the control-plane disable surface (ROADMAP 43.5): disabled providers,
  disabled model refs, and data-egress refusal when the provider's processing
  region differs from the tenant's pinned region.

When the policy refuses, :meth:`ModelCallGate.authorize` raises
:class:`ModelCallDenied` and the caller takes its existing deterministic
fallback, so no request leaves the process.

Why this module exists
----------------------

The auxiliary surfaces (``app/language.py``, ``app/summaries.py``,
``app/copilot.py``) each called ``ModelProvider.complete`` directly with no
policy check at all, and the language detector ran at turn intake *before* the
turn path's own budget/allow-list/disable checks. A tenant that disabled a
provider therefore still egressed through the detector, the translator, the
summariser and the copilot — one transport per surface, per turn.

This gate is the one implementation of those three checks. ``TurnPolicyStage``
delegates to its granular methods so the main chain and the auxiliary calls
cannot drift; the auxiliary callers use :meth:`evaluate`/:meth:`is_allowed`
with the deployment's default model ref, because they carry no prompt-pinned
model.

Fail-open boundaries (unchanged from Phase 19.4 / 43.5)
------------------------------------------------------

An absent tenant policy row, an absent database handle, an absent purpose-ref
or an unreachable control plane all allow the call — the pre-existing
behaviour is preserved so a deployment without a control plane keeps working.
Only an explicit refusal denies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.database import utc_now
from app.model_provider import ModelPolicyDecision, check_model_policy
from app.telemetry import metrics as telemetry_metrics

logger = logging.getLogger("helix")

# The closed catalogue of model-call purposes. A purpose is not decoration: it
# is what a refusal is attributed to in telemetry, and it keeps a typo from
# silently creating an ungoverned call path.
PURPOSE_TRIAGE = "triage"
PURPOSE_LANGUAGE_DETECT = "language_detect"
PURPOSE_LANGUAGE_TRANSLATE = "language_translate"
PURPOSE_SUMMARY = "summary"
PURPOSE_COPILOT_SUGGEST = "copilot_suggest"
PURPOSE_COPILOT_REWRITE = "copilot_rewrite"

MODEL_CALL_PURPOSES = frozenset(
    {
        PURPOSE_TRIAGE,
        PURPOSE_LANGUAGE_DETECT,
        PURPOSE_LANGUAGE_TRANSLATE,
        PURPOSE_SUMMARY,
        PURPOSE_COPILOT_SUGGEST,
        PURPOSE_COPILOT_REWRITE,
    }
)


class ModelCallDenied(RuntimeError):
    """The tenant's policy refuses this model call.

    Callers catch this to take their deterministic path; it is a decision, not
    a failure, so it never propagates to the API surface.
    """

    def __init__(self, *, tenant_id: str | None, purpose: str, reason: str) -> None:
        self.tenant_id = tenant_id
        self.purpose = purpose
        self.reason = reason
        super().__init__(f"model call {purpose!r} denied for tenant {tenant_id!r}: {reason}")


@dataclass(frozen=True)
class ModelGateDecision:
    """Outcome of one policy evaluation against a model-call purpose."""

    allowed: bool
    reason: str = ""
    purpose: str = ""
    model_ref: str | None = None


class ModelCallGate:
    """Evaluates the tenant model policy before any transport.

    ``plane_resolver`` is a zero-argument callable returning the control-plane
    facade (``DataPlaneConfig``) or None. It is resolved on every call rather
    than captured at construction, because deployments and tests attach the
    plane to the orchestrator *after* the gate is built.

    ``default_model_ref`` is the model ref the deployment's provider would use
    for a call that carries none (``Settings.openai_model``); without it a
    ``disabled_models`` entry could never match an auxiliary call, because
    ``check_model_policy`` treats an unattributable ref as unblocked.
    """

    def __init__(
        self,
        database: Any = None,
        *,
        plane_resolver: Callable[[], Any] | None = None,
        default_model_ref: str | None = None,
    ) -> None:
        self.database = database
        self.plane_resolver = plane_resolver
        self.default_model_ref = default_model_ref

    # ------------------------------------------------------------- facets

    def budget_exceeded(self, tenant_id: str | None) -> tuple[bool, int, int | None]:
        """Check the tenant's daily turn budget (Phase 19.4).

        Returns ``(exceeded, used, limit)``; ``limit`` is None when the tenant
        has no budget (unlimited), so ``exceeded`` is False.
        """
        if self.database is None or tenant_id is None:
            return False, 0, None
        try:
            policy = self.database.get_tenant_model_policy(tenant_id)
        except LookupError:
            return False, 0, None
        limit = policy["daily_turn_budget"]
        if limit is None:
            return False, 0, None
        used = self.database.get_tenant_daily_usage(tenant_id, utc_now()[:10])
        return used >= limit, used, limit

    def model_allowed(self, tenant_id: str | None, model_ref: str | None) -> bool:
        """Enforce the tenant's allowed-models allow-list (Phase 19.4).

        ``allowed_models=None`` means unrestricted, and a call without a
        concrete ``model_ref`` is never blocked by the allow-list — the exact
        19.4 semantics the turn path already relies on.
        """
        if self.database is None or tenant_id is None:
            return True
        try:
            policy = self.database.get_tenant_model_policy(tenant_id)
        except LookupError:
            return True
        allowed = policy["allowed_models"]
        if not allowed or not model_ref:
            return True
        return model_ref in allowed

    def governance_decision(
        self, tenant_id: str | None, model_ref: str | None
    ) -> ModelPolicyDecision:
        """Evaluate the 43.5 control-plane disable surface for one model ref.

        Reads ``model_policy`` from the signed control-plane snapshot; without
        a plane, an unreachable plane or no snapshot the check passes — the
        pre-43.5 fail-open default is preserved.
        """
        plane = self.plane_resolver() if self.plane_resolver is not None else None
        if plane is None or tenant_id is None:
            return ModelPolicyDecision(True, "")
        try:
            policy = plane.effective_policy(tenant_id).model_policy or {}
        except Exception:
            logger.info("model governance policy unavailable; skipping 43.5 gate")
            return ModelPolicyDecision(True, "")
        tenant_region = "local"
        if self.database is not None:
            try:
                tenant_region = str(
                    self.database.get_tenant_quota(tenant_id).get("region") or "local"
                )
            except LookupError:
                pass
        return check_model_policy(policy, model_ref, tenant_region=tenant_region)

    # ------------------------------------------------------------ composed

    def evaluate(
        self, tenant_id: str | None, purpose: str, model_ref: str | None = None
    ) -> ModelGateDecision:
        """Compose the three facets into one decision for ``purpose``.

        ``model_ref`` falls back to the deployment default so an auxiliary call
        is still attributable to a concrete model for the disable surface and
        the allow-list.
        """
        if purpose not in MODEL_CALL_PURPOSES:
            raise ValueError(
                f"unknown model call purpose {purpose!r}; allowed: {sorted(MODEL_CALL_PURPOSES)}"
            )
        effective_ref = model_ref or self.default_model_ref
        if tenant_id is None or self.database is None:
            return ModelGateDecision(True, "", purpose, effective_ref)
        exceeded, used, limit = self.budget_exceeded(tenant_id)
        if exceeded:
            reason = f"daily turn budget exhausted ({used}/{limit})"
            return self._deny(tenant_id, purpose, reason, effective_ref)
        if not self.model_allowed(tenant_id, effective_ref):
            reason = f"model {effective_ref!r} is not in the tenant allow-list"
            return self._deny(tenant_id, purpose, reason, effective_ref)
        decision = self.governance_decision(tenant_id, effective_ref)
        if not decision.allowed:
            return self._deny(tenant_id, purpose, decision.reason, effective_ref)
        return ModelGateDecision(True, "", purpose, effective_ref)

    def is_allowed(self, tenant_id: str | None, purpose: str, model_ref: str | None = None) -> bool:
        return self.evaluate(tenant_id, purpose, model_ref).allowed

    def authorize(
        self, tenant_id: str | None, purpose: str, model_ref: str | None = None
    ) -> ModelGateDecision:
        """Like :meth:`evaluate` but raises :class:`ModelCallDenied` on refusal."""
        decision = self.evaluate(tenant_id, purpose, model_ref)
        if not decision.allowed:
            raise ModelCallDenied(
                tenant_id=tenant_id, purpose=decision.purpose, reason=decision.reason
            )
        return decision

    # ------------------------------------------------------------- internal

    def _deny(
        self, tenant_id: str, purpose: str, reason: str, model_ref: str | None
    ) -> ModelGateDecision:
        # A refusal must be visible (H04: "失败要可见") without needing the
        # caller to audit — the auxiliary surfaces have no conversation
        # context to attach an audit event to.
        telemetry_metrics.increment(
            "model.call_denied", tenant_id=tenant_id, purpose=purpose, reason=reason
        )
        logger.info(
            "model.call_denied",
            extra={"tenant_id": tenant_id, "purpose": purpose, "reason": reason},
        )
        return ModelGateDecision(False, reason, purpose, model_ref)


__all__ = [
    "MODEL_CALL_PURPOSES",
    "PURPOSE_COPILOT_REWRITE",
    "PURPOSE_COPILOT_SUGGEST",
    "PURPOSE_LANGUAGE_DETECT",
    "PURPOSE_LANGUAGE_TRANSLATE",
    "PURPOSE_SUMMARY",
    "PURPOSE_TRIAGE",
    "ModelCallDenied",
    "ModelCallGate",
    "ModelGateDecision",
]
