"""Fail-closed, step-level authorization for shopping Agent tool calls.

The policy layer validates a call immediately before execution. It owns no
tool business logic and never treats model- or user-provided policy fields as
authority.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from app.routers.AImodel.plan_models import (
    AgentPlan,
    PlanStep,
    PlanTool,
    RiskLevel,
    StepType,
)
from app.routers.AImodel.tools import (
    SAFE_PRODUCT_ITEM_ID_PATTERN,
    parse_item_id_from_link,
)

TOOL_POLICY_VERSION = "d3-tool-policy-v1"

_ITEM_ID_PATTERN = rf"^{SAFE_PRODUCT_ITEM_ID_PATTERN}$"
_COLLECTION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_HOST_LABEL_PATTERN = re.compile(
    r"^(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)$",
    re.IGNORECASE,
)
_BLOCKED_HOST_SUFFIXES = (".internal", ".local", ".localhost")
_PUBLIC_ARGUMENT_KEYS = frozenset(
    {
        "collections",
        "conversation_id",
        "include_image_base64",
        "item_id",
        "item_ids",
        "max_results",
        "no_rerank",
        "order_id",
        "product_urls",
        "quantity",
        "query",
        "top_k",
        "user_id",
    }
)

ItemId = Annotated[str, Field(min_length=1, max_length=128, pattern=_ITEM_ID_PATTERN)]
CollectionName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=_COLLECTION_PATTERN),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class AgentToolName(StrEnum):
    """Closed server registry, including tools reserved for later stages."""

    PRODUCT_SEARCH = "product_search"
    PRODUCT_SNAPSHOT = "product_snapshot"
    PRODUCT_REVIEWS = "product_reviews"
    RAG_LOOKUP = "rag_lookup"
    WEB_SEARCH = "web_search"
    ORDER_LOOKUP = "order_lookup"
    ACTION_PREVIEW = "action_preview"
    CART_WRITE = "cart_write"


class ToolAccessMode(StrEnum):
    """Immutable side-effect classification used by executor policy."""

    READ = "read"
    WRITE_PREVIEW = "write_preview"
    WRITE = "write"


class ToolAuthorizationCode(StrEnum):
    """Stable machine-readable outcomes with no internal policy details."""

    ALLOWED = "allowed"
    PLAN_STEP_NOT_FOUND = "plan_step_not_found"
    STEP_MISMATCH = "step_mismatch"
    UNKNOWN_TOOL = "unknown_tool"
    STEP_TOOL_NOT_ALLOWED = "step_tool_not_allowed"
    PLAN_TOOL_NOT_DECLARED = "plan_tool_not_declared"
    RISK_MISMATCH = "risk_mismatch"
    INVALID_ARGUMENTS = "invalid_arguments"
    USER_SCOPE_MISMATCH = "user_scope_mismatch"
    CONVERSATION_SCOPE_MISMATCH = "conversation_scope_mismatch"
    ITEM_PROVENANCE_REQUIRED = "item_provenance_required"
    ITEM_NOT_IN_CANDIDATE_SET = "item_not_in_candidate_set"
    UNSAFE_URL = "unsafe_url"
    CONFIRMATION_REQUIRED = "confirmation_required"
    INVALID_CONFIRMATION = "invalid_confirmation"


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class _ScopedToolInput(_StrictModel):
    user_id: int = Field(gt=0, strict=True)
    conversation_id: int = Field(gt=0, strict=True)


class ProductSearchToolInput(_ScopedToolInput):
    query: str = Field(min_length=1, max_length=512)


class ProductSnapshotToolInput(_ScopedToolInput):
    item_ids: tuple[ItemId, ...] = Field(default=(), max_length=50)
    product_urls: tuple[Annotated[str, Field(max_length=2_048)], ...] = Field(
        default=(),
        max_length=50,
    )

    @model_validator(mode="after")
    def require_products(self) -> ProductSnapshotToolInput:
        if not self.item_ids and not self.product_urls:
            raise ValueError("at least one product reference is required")
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("duplicate item ids are not allowed")
        return self


class ProductReviewsToolInput(_ScopedToolInput):
    item_ids: tuple[ItemId, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def reject_duplicate_items(self) -> ProductReviewsToolInput:
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("duplicate item ids are not allowed")
        return self


class RagLookupToolInput(_ScopedToolInput):
    query: str = Field(min_length=1, max_length=2_000)
    collections: tuple[CollectionName, ...] = Field(default=(), max_length=8)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    no_rerank: bool = Field(default=False, strict=True)
    include_image_base64: Literal[False] = False


class WebSearchToolInput(_ScopedToolInput):
    query: str = Field(min_length=1, max_length=512)
    max_results: int = Field(default=5, ge=1, le=10, strict=True)


class OrderLookupToolInput(_ScopedToolInput):
    order_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )


class ActionPreviewToolInput(_ScopedToolInput):
    item_id: ItemId
    quantity: int = Field(ge=1, le=99, strict=True)


class CartWriteToolInput(ActionPreviewToolInput):
    confirmation_token: str = Field(min_length=16, max_length=2_048)


AgentToolInput = (
    ProductSearchToolInput
    | ProductSnapshotToolInput
    | ProductReviewsToolInput
    | RagLookupToolInput
    | WebSearchToolInput
    | OrderLookupToolInput
    | ActionPreviewToolInput
    | CartWriteToolInput
)


class AgentToolCall(_StrictModel):
    """Untrusted model-requested tool call before authorization."""

    tool_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$",
    )
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=32)


class AgentToolPublicResult(_StrictModel):
    """Common public result envelope shared by every Agent tool adapter."""

    schema_version: Literal["1.0"] = "1.0"
    tool_name: AgentToolName
    status: ToolResultStatus
    data: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)
    error_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=_ERROR_CODE_PATTERN,
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> AgentToolPublicResult:
        if self.status is ToolResultStatus.SUCCESS and self.error_code is not None:
            raise ValueError("successful tool results cannot contain error_code")
        if self.status is ToolResultStatus.ERROR:
            if self.error_code is None:
                raise ValueError("failed tool results require error_code")
            if self.data:
                raise ValueError("failed tool results cannot expose partial data")
        return self


class ToolAuthorizationContext(_StrictModel):
    """Trusted request scope assembled by the server, never by the model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    user_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    candidate_item_ids: tuple[ItemId, ...] = Field(default=(), max_length=100)
    trace_context: AgentTraceContext | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def reject_duplicate_candidates(self) -> ToolAuthorizationContext:
        if len(set(self.candidate_item_ids)) != len(self.candidate_item_ids):
            raise ValueError("duplicate candidate item ids are not allowed")
        return self


class ToolAuthorizationDecision(_StrictModel):
    """Safe allow/deny result consumed by the executor and Trace."""

    policy_version: Literal["d3-tool-policy-v1"] = TOOL_POLICY_VERSION
    allowed: bool
    code: ToolAuthorizationCode
    step_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    access_mode: ToolAccessMode | None = None
    parameter_summary: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_outcome(self) -> ToolAuthorizationDecision:
        if self.allowed != (self.code is ToolAuthorizationCode.ALLOWED):
            raise ValueError("allowed flag and authorization code disagree")
        return self


class StepPolicyDenied(PermissionError):
    """Stop execution while exposing only the stable public decision."""

    def __init__(self, decision: ToolAuthorizationDecision) -> None:
        self.decision = decision
        super().__init__(
            f"tool_call_denied:{decision.code.value}:"
            f"{decision.step_id}:{decision.tool_name}"
        )


ConfirmationVerifier = Callable[
    [CartWriteToolInput, ToolAuthorizationContext],
    bool,
]


class _ToolPolicyDefinition(_StrictModel):
    input_model: type[_ScopedToolInput]
    access_mode: ToolAccessMode
    risk_level: RiskLevel
    plan_tool: PlanTool | None
    checks_candidates: bool = False
    checks_urls: bool = False


_TOOL_POLICIES: Mapping[AgentToolName, _ToolPolicyDefinition] = MappingProxyType(
    {
        AgentToolName.PRODUCT_SEARCH: _ToolPolicyDefinition(
            input_model=ProductSearchToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=PlanTool.PRODUCT_SEARCH,
        ),
        AgentToolName.PRODUCT_SNAPSHOT: _ToolPolicyDefinition(
            input_model=ProductSnapshotToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=PlanTool.PRODUCT_SNAPSHOT,
            checks_candidates=True,
            checks_urls=True,
        ),
        AgentToolName.PRODUCT_REVIEWS: _ToolPolicyDefinition(
            input_model=ProductReviewsToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=PlanTool.PRODUCT_REVIEWS,
            checks_candidates=True,
        ),
        AgentToolName.RAG_LOOKUP: _ToolPolicyDefinition(
            input_model=RagLookupToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=PlanTool.RAG_LOOKUP,
        ),
        AgentToolName.WEB_SEARCH: _ToolPolicyDefinition(
            input_model=WebSearchToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=None,
        ),
        AgentToolName.ORDER_LOOKUP: _ToolPolicyDefinition(
            input_model=OrderLookupToolInput,
            access_mode=ToolAccessMode.READ,
            risk_level=RiskLevel.MEDIUM,
            plan_tool=None,
        ),
        AgentToolName.ACTION_PREVIEW: _ToolPolicyDefinition(
            input_model=ActionPreviewToolInput,
            access_mode=ToolAccessMode.WRITE_PREVIEW,
            risk_level=RiskLevel.HIGH,
            plan_tool=PlanTool.ACTION_PREVIEW,
            checks_candidates=True,
        ),
        AgentToolName.CART_WRITE: _ToolPolicyDefinition(
            input_model=CartWriteToolInput,
            access_mode=ToolAccessMode.WRITE,
            risk_level=RiskLevel.HIGH,
            plan_tool=None,
            checks_candidates=True,
        ),
    }
)

_STEP_TOOL_ALLOWLIST: Mapping[StepType, frozenset[AgentToolName]] = MappingProxyType(
    {
        StepType.CLARIFY: frozenset(),
        StepType.PRODUCT_SEARCH: frozenset({AgentToolName.PRODUCT_SEARCH}),
        StepType.SNAPSHOT: frozenset({AgentToolName.PRODUCT_SNAPSHOT}),
        StepType.REVIEW_FETCH: frozenset({AgentToolName.PRODUCT_REVIEWS}),
        StepType.RAG_LOOKUP: frozenset({AgentToolName.RAG_LOOKUP}),
        StepType.FILTER: frozenset(),
        StepType.RANK: frozenset(),
        StepType.COMPARE: frozenset(),
        StepType.COMPOSE: frozenset(),
        StepType.ACTION_PREVIEW: frozenset({AgentToolName.ACTION_PREVIEW}),
    }
)


class StepPolicyGate:
    """Re-authorize each proposed tool call against trusted runtime state."""

    def __init__(
        self,
        *,
        confirmation_verifier: ConfirmationVerifier | None = None,
    ) -> None:
        self._confirmation_verifier = confirmation_verifier

    def access_mode_for(self, tool_name: AgentToolName | str) -> ToolAccessMode:
        """Return the immutable server-side side-effect classification."""

        return _TOOL_POLICIES[AgentToolName(tool_name)].access_mode

    def authorize(
        self,
        plan: AgentPlan,
        step: PlanStep,
        tool_call: AgentToolCall,
        context: ToolAuthorizationContext,
    ) -> ToolAuthorizationDecision:
        """Return a deterministic decision and record a privacy-safe event."""

        summary = _summarize_arguments(tool_call.arguments)
        try:
            tool_name = AgentToolName(tool_call.tool_name)
        except ValueError:
            return self._decision(
                code=ToolAuthorizationCode.UNKNOWN_TOOL,
                step=step,
                tool_name=tool_call.tool_name,
                summary=summary,
                context=context,
            )

        definition = _TOOL_POLICIES[tool_name]
        plan_step = next(
            (
                candidate
                for candidate in plan.steps
                if candidate.step_id == step.step_id
            ),
            None,
        )
        if plan_step is None:
            return self._decision(
                code=ToolAuthorizationCode.PLAN_STEP_NOT_FOUND,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if plan_step != step:
            return self._decision(
                code=ToolAuthorizationCode.STEP_MISMATCH,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if tool_name not in _STEP_TOOL_ALLOWLIST[step.step_type]:
            return self._decision(
                code=ToolAuthorizationCode.STEP_TOOL_NOT_ALLOWED,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if (
            definition.plan_tool is None
            or definition.plan_tool not in step.allowed_tools
        ):
            return self._decision(
                code=ToolAuthorizationCode.PLAN_TOOL_NOT_DECLARED,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if step.risk_level is not definition.risk_level:
            return self._decision(
                code=ToolAuthorizationCode.RISK_MISMATCH,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )

        try:
            parsed_input = definition.input_model.model_validate(tool_call.arguments)
        except ValidationError:
            return self._decision(
                code=ToolAuthorizationCode.INVALID_ARGUMENTS,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if parsed_input.user_id != context.user_id:
            return self._decision(
                code=ToolAuthorizationCode.USER_SCOPE_MISMATCH,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )
        if parsed_input.conversation_id != context.conversation_id:
            return self._decision(
                code=ToolAuthorizationCode.CONVERSATION_SCOPE_MISMATCH,
                step=step,
                tool_name=tool_name,
                definition=definition,
                summary=summary,
                context=context,
            )

        if definition.checks_urls:
            for product_url in getattr(parsed_input, "product_urls", ()):
                if not _is_safe_public_url(product_url):
                    return self._decision(
                        code=ToolAuthorizationCode.UNSAFE_URL,
                        step=step,
                        tool_name=tool_name,
                        definition=definition,
                        summary=summary,
                        context=context,
                    )
                if _item_id_from_product_url(product_url) is None:
                    return self._decision(
                        code=ToolAuthorizationCode.ITEM_PROVENANCE_REQUIRED,
                        step=step,
                        tool_name=tool_name,
                        definition=definition,
                        summary=summary,
                        context=context,
                    )

        if definition.checks_candidates:
            requested_items = _requested_item_ids(parsed_input)
            if not requested_items:
                return self._decision(
                    code=ToolAuthorizationCode.ITEM_PROVENANCE_REQUIRED,
                    step=step,
                    tool_name=tool_name,
                    definition=definition,
                    summary=summary,
                    context=context,
                )
            if not requested_items.issubset(set(context.candidate_item_ids)):
                return self._decision(
                    code=ToolAuthorizationCode.ITEM_NOT_IN_CANDIDATE_SET,
                    step=step,
                    tool_name=tool_name,
                    definition=definition,
                    summary=summary,
                    context=context,
                )

        if definition.access_mode is ToolAccessMode.WRITE:
            if self._confirmation_verifier is None:
                return self._decision(
                    code=ToolAuthorizationCode.CONFIRMATION_REQUIRED,
                    step=step,
                    tool_name=tool_name,
                    definition=definition,
                    summary=summary,
                    context=context,
                )
            if not isinstance(parsed_input, CartWriteToolInput) or not (
                self._confirmation_verifier(parsed_input, context)
            ):
                return self._decision(
                    code=ToolAuthorizationCode.INVALID_CONFIRMATION,
                    step=step,
                    tool_name=tool_name,
                    definition=definition,
                    summary=summary,
                    context=context,
                )

        return self._decision(
            code=ToolAuthorizationCode.ALLOWED,
            step=step,
            tool_name=tool_name,
            definition=definition,
            summary=summary,
            context=context,
        )

    def enforce(
        self,
        plan: AgentPlan,
        step: PlanStep,
        tool_call: AgentToolCall,
        context: ToolAuthorizationContext,
    ) -> ToolAuthorizationDecision:
        """Return an allow decision or raise before the caller invokes a tool."""

        decision = self.authorize(plan, step, tool_call, context)
        if not decision.allowed:
            raise StepPolicyDenied(decision)
        return decision

    @staticmethod
    def _decision(
        *,
        code: ToolAuthorizationCode,
        step: PlanStep,
        tool_name: AgentToolName | str,
        summary: dict[str, JsonValue],
        context: ToolAuthorizationContext,
        definition: _ToolPolicyDefinition | None = None,
    ) -> ToolAuthorizationDecision:
        decision = ToolAuthorizationDecision(
            allowed=code is ToolAuthorizationCode.ALLOWED,
            code=code,
            step_id=step.step_id,
            tool_name=str(tool_name),
            access_mode=definition.access_mode if definition else None,
            parameter_summary=summary,
        )
        trace = context.trace_context
        if trace is not None:
            event = trace.begin_event(
                AgentTraceEventType.TOOL_CALL,
                stage="step_policy",
                summary={
                    "policy_version": TOOL_POLICY_VERSION,
                    "authorization_code": code.value,
                    "allowed": decision.allowed,
                    "access_mode": (
                        decision.access_mode.value if decision.access_mode else None
                    ),
                    "parameter_summary": summary,
                },
                related_ids={"step_id": step.step_id},
                tool_name=decision.tool_name,
            )
            event.finish(
                AgentTraceStatus.SUCCESS
                if decision.allowed
                else AgentTraceStatus.ERROR,
                error=None if decision.allowed else code.value,
            )
        return decision


def _requested_item_ids(parsed_input: _ScopedToolInput) -> set[str]:
    requested: set[str] = set()
    item_id = getattr(parsed_input, "item_id", None)
    if item_id:
        requested.add(item_id)
    requested.update(getattr(parsed_input, "item_ids", ()))
    for product_url in getattr(parsed_input, "product_urls", ()):
        item_id = _item_id_from_product_url(product_url)
        if item_id is not None:
            requested.add(item_id)
    return requested


def _item_id_from_product_url(value: str) -> str | None:
    return parse_item_id_from_link(value)


def _is_safe_public_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    if parsed.username or parsed.password or port not in {None, 443}:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(host) > 253 or len(labels) < 2:
            return False
        if any(not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels):
            return False
        if all(_NUMERIC_HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
            return False
        return any(character.isalpha() for character in labels[-1])
    return address.is_global


def _summarize_arguments(arguments: Mapping[str, Any]) -> dict[str, JsonValue]:
    summary: dict[str, JsonValue] = {
        "argument_keys": sorted(
            str(key) for key in arguments if str(key) in _PUBLIC_ARGUMENT_KEYS
        )[:32]
    }
    query = arguments.get("query")
    if isinstance(query, str):
        summary["query_chars"] = len(query)
    item_count = 0
    if isinstance(arguments.get("item_id"), str):
        item_count += 1
    item_ids = arguments.get("item_ids")
    if isinstance(item_ids, (list, tuple)):
        item_count += len(item_ids)
    if item_count:
        summary["item_count"] = min(item_count, 1_000)
    urls = arguments.get("product_urls")
    if isinstance(urls, (list, tuple)):
        summary["url_count"] = min(len(urls), 1_000)
    return summary
