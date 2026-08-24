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
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.residency import resolve_region


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderMetadata:
    """Governance declarations for one model provider (ROADMAP 43.5).

    ``data_retention`` states how long prompts/completions are retained by
    the vendor; ``training_opt_out`` is True when no customer content may be
    used for vendor training; ``region`` names where inference happens so a
    tenant pinned to another region can refuse egress to it.
    """

    name: str
    data_retention: str = "vendor-managed"
    training_opt_out: bool = True
    region: str = "local"


@dataclass(frozen=True)
class ModelPolicyDecision:
    """Outcome of a tenant policy check against one model/provider pair."""

    allowed: bool
    reason: str = ""


# Registry of declared providers; deployments replace entries for real
# vendors, keeping the module free of environment reads.
PROVIDER_METADATA: dict[str, ProviderMetadata] = {
    "openai": ProviderMetadata(
        name="openai",
        data_retention="30-days-zero-retention-available",
        training_opt_out=True,
        region="us",
    ),
}


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


class ModelProvider(Protocol):
    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> str: ...


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

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
        # ``model_ref`` from a prompt version (Phase 19.1) selects the model
        # for this turn; without one the settings default applies.
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
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelProviderError("Model provider returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("Model provider returned empty content")
        return content


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

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
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
    "ChainedModelProvider",
    "ModelPolicyDecision",
    "ModelProvider",
    "ModelProviderError",
    "PROVIDER_METADATA",
    "OpenAICompatibleProvider",
    "ProviderMetadata",
    "check_model_policy",
    "provider_for_model_ref",
]
