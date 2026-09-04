"""Model provider boundary with governance metadata (ROADMAP 43.5).

A :class:`~app.model_provider.ModelProvider` is the single seam between the
deterministic agents and any external model.  ROADMAP 43.5 item 3 adds a
declarative **provider metadata** surface (:class:`ProviderMetadata`): every
provider records the data-retention posture, the training opt-out flag, and
the processing region it operates in, so tenant policy can disable a whole
provider (or route around it) without reading vendor documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.residency import resolve_region


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PricingTier:
    """Per-model pricing for one provider (ROADMAP 2.3.1).

    Costs are USD per 1,000 tokens, the de-facto vendor billing unit.
    ``context_window`` is the model's maximum prompt+completion token
    capacity. Only entries that are actually used in production need to be
    accurate; unlisted refs fall back to the provider's ``default`` tier.
    """

    input_cost_per_1k_tokens: float
    output_cost_per_1k_tokens: float
    context_window: int = 128_000


@dataclass(frozen=True)
class ProviderMetadata:
    """Governance declarations for one model provider (ROADMAP 43.5).

    ``data_retention`` states how long prompts/completions are retained by
    the vendor; ``training_opt_out`` is True when no customer content may be
    used for vendor training; ``region`` names where inference happens so a
    tenant pinned to another region can refuse egress to it. ``pricing`` —
    added for ROADMAP 2.3.1 — maps a model ref to its :class:`PricingTier`;
    ``default_pricing`` applies to any ref without an explicit tier.
    """

    name: str
    data_retention: str = "vendor-managed"
    training_opt_out: bool = True
    region: str = "local"
    pricing: dict[str, PricingTier] | None = None
    default_pricing: PricingTier | None = None


@dataclass(frozen=True)
class ModelPolicyDecision:
    """Outcome of a tenant policy check against one model/provider pair."""

    allowed: bool
    reason: str = ""


# Registry of declared providers; deployments replace entries for real
# vendors, keeping the module free of environment reads.
# 2.3.1: pricing is USD per 1K tokens from vendor published rate cards.
# Unlisted refs (e.g. "gpt-4.1-mini") fall back to ``default_pricing`` so
# cost attribution never fails on a missing entry — only on a missing
# provider registry record.
PROVIDER_METADATA: dict[str, ProviderMetadata] = {
    "openai": ProviderMetadata(
        name="openai",
        data_retention="30-days-zero-retention-available",
        training_opt_out=True,
        region="us",
        pricing={
            "gpt-4o": PricingTier(2.50, 10.00, 128_000),
            "gpt-4o-mini": PricingTier(0.15, 0.60, 128_000),
            "gpt-4.1": PricingTier(2.00, 8.00, 1_047_576),
            "gpt-4.1-mini": PricingTier(0.40, 1.60, 1_047_576),
            "gpt-4.1-nano": PricingTier(0.10, 0.40, 1_047_576),
            "o3": PricingTier(2.00, 8.00, 200_000),
            "o3-mini": PricingTier(0.40, 1.60, 200_000),
            "o4-mini": PricingTier(0.40, 1.60, 200_000),
        },
        default_pricing=PricingTier(2.50, 10.00, 128_000),
    ),
}


def pricing_for_model_ref(
    provider_name: str | None, model_ref: str | None
) -> PricingTier | None:
    """Resolve the pricing tier for a model ref under a provider.

    Returns ``None`` when the provider (or its pricing surface) is unknown —
    callers then treat the inference cost as uncomputed rather than guessing.
    """
    if not provider_name:
        return None
    metadata = PROVIDER_METADATA.get(provider_name)
    if metadata is None or metadata.pricing is None:
        return None
    if model_ref:
        tier = metadata.pricing.get(model_ref)
        if tier is not None:
            return tier
    return metadata.default_pricing


def provider_for_model_ref(model_ref: str | None) -> str | None:
    """Infer the owning provider from a ``provider/model`` style ref.

    A ref carrying an explicit ``/`` or ``:`` prefix maps to that provider;
    bare refs are attributed to the settings default (``openai``) because the
    only wired provider family today is OpenAI-compatible. Returns None when
    nothing can be attributed — callers treat None as "no provider gate".
    """
    if not model_ref:
        return None
    if "/" not in model_ref and ":" not in model_ref:
        return "openai"
    head = model_ref.split("/", 1)[0].split(":", 1)[0].strip().lower()
    return head or "openai"


def check_model_policy(
    model_policy: dict[str, Any] | None,
    model_ref: str | None,
    *,
    tenant_region: str = "local",
) -> ModelPolicyDecision:
    """Evaluate the 43.5 tenant disable-surface for one model ref.

    ``model_policy`` is the (control-plane) policy document; recognised keys:

    - ``disabled_providers`` — list of provider names that may not serve
      this tenant at all;
    - ``disabled_models``   — list of model refs refused alongside any
      allow-list already enforced by the 19.4 path;
    - ``allow_data_egress`` — False refuses every provider whose declared
      region differs from the tenant's pinned region.

    An empty/absent policy allows everything (backward-compatible default),
    and an unattributable model ref is never blocked by the provider/region
    facets — only by an exact ``disabled_models`` entry.
    """
    policy = model_policy or {}
    disabled_models = {str(m) for m in policy.get("disabled_models") or []}
    if model_ref and model_ref in disabled_models:
        return ModelPolicyDecision(False, f"model {model_ref!r} is disabled for this tenant")
    provider_name = provider_for_model_ref(model_ref)
    if not provider_name:
        return ModelPolicyDecision(True, "")
    if str(provider_name) in {str(p) for p in policy.get("disabled_providers") or []}:
        return ModelPolicyDecision(False, f"provider {provider_name!r} is disabled for this tenant")
    if policy.get("allow_data_egress") is False:
        metadata = PROVIDER_METADATA.get(provider_name)
        region = resolve_region(metadata.region if metadata else None)
        if region != resolve_region(tenant_region):
            return ModelPolicyDecision(
                False,
                f"provider {provider_name!r} processes data in region "
                f"{region!r}, outside tenant region {resolve_region(tenant_region)!r}",
            )
    return ModelPolicyDecision(True, "")


@dataclass(frozen=True)
class ModelResponse:
    """Structured completion result (ROADMAP 2.3.2).

    ``content`` is the raw completion text; ``usage`` carries vendor-reported
    token counts when available; ``cost_usd`` is computed from the provider's
    declared :class:`PricingTier` and is ``None`` when no pricing is known
    (never guessed). ``model``/``provider`` are the resolved refs so the
    attribution surface does not depend on caller-side bookkeeping.
    """

    content: str
    usage: dict[str, int] | None = None
    model: str | None = None
    provider: str | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    model_ref: str | None = None


class ModelProvider(Protocol):
    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse: ...


class OpenAICompatibleProvider:
    """Optional model boundary; evidence-first deterministic agents remain the default.

    Exposes this deployment's :class:`ProviderMetadata` under ``metadata`` so
    policy checks can read the retention/opt-out/region posture without
    knowing the concrete vendor class.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self.settings = settings

    @property
    def metadata(self) -> ProviderMetadata:
        declared = PROVIDER_METADATA.get("openai")
        if declared is not None:
            return declared
        return ProviderMetadata(name="openai")

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> ModelResponse:
        # ``model_ref`` from a prompt version (Phase 19.1) selects the model
        # for this turn; without one the settings default applies.
        started = monotonic()
        model = model_ref or self.settings.openai_model
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=5)) as client:
                response = client.post(
                    f"{self.settings.openai_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    json={
                        "model": model,
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelProviderError("Model provider request failed") from exc
        latency_ms = max(0, int((monotonic() - started) * 1000))
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelProviderError("Model provider returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("Model provider returned empty content")
        usage = self._extract_usage(payload)
        provider_name = "openai"
        tier = pricing_for_model_ref(provider_name, model)
        cost_usd: float | None = None
        if tier is not None and usage is not None:
            cost_usd = (
                (usage.get("prompt_tokens", 0) / 1000) * tier.input_cost_per_1k_tokens
                + (usage.get("completion_tokens", 0) / 1000) * tier.output_cost_per_1k_tokens
            )
        return ModelResponse(
            content=content,
            usage=usage,
            model=model,
            provider=provider_name,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            model_ref=model_ref,
        )

    @staticmethod
    def _extract_usage(payload: Any) -> dict[str, int] | None:
        """Parse the OpenAI ``usage`` block, tolerating absent/odd shapes."""
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if not isinstance(prompt, int) or not isinstance(completion, int):
            return None
        return {"prompt_tokens": prompt, "completion_tokens": completion}


class ChainedModelProvider:
    """A ModelProvider that fails over across an ordered provider chain.

    Phase 19.5: when the primary model provider times out or errors, the next
    provider in the chain is tried; if every provider fails, the last error
    is re-raised so the caller (``TriageAgent``) falls back to the
    deterministic routing path. This keeps a single supplier outage from
    breaking the routing contract.
    """

    def __init__(self, providers: list[ModelProvider]) -> None:
        if not providers:
            raise ValueError("ChainedModelProvider requires at least one provider")
        self._providers = list(providers)

    @property
    def providers(self) -> list[ModelProvider]:
        return list(self._providers)

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> ModelResponse:
        last_error: ModelProviderError | None = None
        for provider in self._providers:
            try:
                complete = provider.complete
                if model_ref is None:
                    # Preserve the 2-argument call for providers/stubs that
                    # predate model_ref selection (Phase 19.4).
                    return complete(system_prompt, user_prompt)
                try:
                    return complete(system_prompt, user_prompt, model_ref)
                except TypeError:
                    # Older provider without the model_ref parameter.
                    return complete(system_prompt, user_prompt)
            except ModelProviderError as exc:
                last_error = exc
                continue
        assert last_error is not None  # providers non-empty => at least one attempt
        raise last_error


__all__ = [
    "PROVIDER_METADATA",
    "ChainedModelProvider",
    "ModelPolicyDecision",
    "ModelProvider",
    "ModelProviderError",
    "ModelResponse",
    "OpenAICompatibleProvider",
    "PricingTier",
    "ProviderMetadata",
    "check_model_policy",
    "pricing_for_model_ref",
    "provider_for_model_ref",
]
