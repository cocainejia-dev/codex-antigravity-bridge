"""Autonomous decision policy and classification for VNext execution.

Provides pure, auditable evaluation functions and typed DecisionRecord contracts
for autonomous agent governance, classifying tasks into AUTO_DECIDE, CODEX_DECIDE,
and HUMAN_DECISION_REQUIRED tiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any
import uuid

from .contracts import (
    CREDENTIAL_PATTERNS,
    RiskClass,
    _format_timestamp,
    _utc_now_iso,
    normalize_path,
    normalize_paths,
    validate_no_credentials,
)


class DecisionTier(str, Enum):
    """Decision authority tier."""

    AUTO_DECIDE = "AUTO_DECIDE"
    CODEX_DECIDE = "CODEX_DECIDE"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"

    @classmethod
    def from_value(cls, val: str | DecisionTier) -> DecisionTier:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid decision tier type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown decision tier: {val!r}")


class DecisionCategory(str, Enum):
    """Specific classification categories for decisions."""

    # AUTO_DECIDE categories
    ORDINARY_REFACTOR = "ORDINARY_REFACTOR"
    TESTS_AND_FIXTURES = "TESTS_AND_FIXTURES"
    LOCAL_BUG_FIX = "LOCAL_BUG_FIX"
    EXISTING_ARCHITECTURE = "EXISTING_ARCHITECTURE"
    MINIMAL_REVERSIBLE_CHANGE = "MINIMAL_REVERSIBLE_CHANGE"

    # CODEX_DECIDE categories
    MODULE_BOUNDARY = "MODULE_BOUNDARY"
    PUBLIC_ABSTRACTION = "PUBLIC_ABSTRACTION"
    REPOSITORY_PROVIDER_DESIGN = "REPOSITORY_PROVIDER_DESIGN"
    CACHE_CONCURRENCY_STRATEGY = "CACHE_CONCURRENCY_STRATEGY"
    COMPETING_ARCHITECTURES = "COMPETING_ARCHITECTURES"
    GENERAL_UNCERTAINTY = "GENERAL_UNCERTAINTY"

    # HUMAN_DECISION_REQUIRED categories
    REAL_FUNDS_OR_TRADES = "REAL_FUNDS_OR_TRADES"
    CREDENTIAL_SECURITY_BOUNDARY = "CREDENTIAL_SECURITY_BOUNDARY"
    IRREVERSIBLE_DATA_OR_MIGRATION = "IRREVERSIBLE_DATA_OR_MIGRATION"
    MAJOR_PRODUCT_CONFLICT = "MAJOR_PRODUCT_CONFLICT"
    LEGAL_COMPLIANCE_RISK = "LEGAL_COMPLIANCE_RISK"
    REPEATED_UNRESOLVED_REPAIR = "REPEATED_UNRESOLVED_REPAIR"
    EXPLICIT_HUMAN_REQUEST = "EXPLICIT_HUMAN_REQUEST"

    @classmethod
    def from_value(cls, val: str | DecisionCategory) -> DecisionCategory:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid decision category type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown decision category: {val!r}")


# Deterministic pattern rules for intent analysis

HUMAN_PATTERNS: tuple[tuple[re.Pattern[str], DecisionCategory, str], ...] = (
    (
        re.compile(r"(?i)\b(real\s+funds?|live\s+trading|place\s+orders?|broker\s+(?:account|permission|api|credentials?)|real\s+money|live\s+execution|wire\s+transfer|wallet\s+transfer|stock\s+order|buy\s+shares?|sell\s+shares?)\b"),
        DecisionCategory.REAL_FUNDS_OR_TRADES,
        "Involves real funds, live market trading, or broker permissions.",
    ),
    (
        re.compile(r"(?i)\b(credential\s+boundary|security\s+boundary|auth(?:entication)?\s+bypass|rotate\s+(?:master\s+)?key|production\s+secrets?|private\s+key\s+store|iam\s+privilege\s+escalation)\b"),
        DecisionCategory.CREDENTIAL_SECURITY_BOUNDARY,
        "Involves credentials, master keys, authentication bypass, or security boundaries.",
    ),
    (
        re.compile(r"(?i)\b(drop\s+(?:database|table|schema)|truncate\s+table|delete\s+from\s+[a-z0-9_]+\s*;|rm\s+-rf\s+[/~]|irreversible\s+migration|purge\s+production\s+data|destroy\s+cluster|wipe\s+database)\b"),
        DecisionCategory.IRREVERSIBLE_DATA_OR_MIGRATION,
        "Involves irreversible database migration, table dropping, or destructive data purge.",
    ),
    (
        re.compile(r"(?i)\b(major\s+product\s+conflict|contradictory\s+requirements?|fundamental\s+spec\s+conflict|incompatible\s+business\s+rules?|conflicting\s+user\s+specs?)\b"),
        DecisionCategory.MAJOR_PRODUCT_CONFLICT,
        "Involves major product conflicts or irreconcilable business requirements.",
    ),
    (
        re.compile(r"(?i)\b(legal\s+compliance|regulatory\s+violation|gdpr\s+breach|sec\s+compliance|license\s+violation|insider\s+trading|copyright\s+infringement)\b"),
        DecisionCategory.LEGAL_COMPLIANCE_RISK,
        "Involves legal, regulatory, or compliance high-risk domain.",
    ),
)

CODEX_PATTERNS: tuple[tuple[re.Pattern[str], DecisionCategory, str], ...] = (
    (
        re.compile(r"(?i)\b(module\s+boundary|package\s+structure|restructure\s+modules?|cross-module|subpackage\s+refactor|reorganize\s+packages?)\b"),
        DecisionCategory.MODULE_BOUNDARY,
        "Architectural decision involving module boundaries and package hierarchy.",
    ),
    (
        re.compile(r"(?i)\b(public\s+abstraction|public\s+api|interface\s+contract|abstract\s+base\s+class|public\s+signature|public\s+interface|export\s+contract)\b"),
        DecisionCategory.PUBLIC_ABSTRACTION,
        "Architectural decision involving public abstractions and interface contracts.",
    ),
    (
        re.compile(r"(?i)\b(repository\s+pattern|repository\s+design|provider\s+design|storage\s+backend|data\s+access\s+layer|service\s+provider\s+abstraction)\b"),
        DecisionCategory.REPOSITORY_PROVIDER_DESIGN,
        "Architectural decision regarding repository pattern or provider abstractions.",
    ),
    (
        re.compile(r"(?i)\b(cache\s+strategy|caching\s+policy|concurrency\s+model|async\s+vs\s+threading|thread\s+pool\s+size|distributed\s+lock|locking\s+strategy|lock\s+granularity)\b"),
        DecisionCategory.CACHE_CONCURRENCY_STRATEGY,
        "Architectural decision regarding cache or concurrency strategies.",
    ),
    (
        re.compile(r"(?i)\b(competing\s+architectures?|architectural\s+trade-?offs?|alternative\s+designs?|design\s+alternatives?|architectural\s+decision|design\s+trade-?off)\b"),
        DecisionCategory.COMPETING_ARCHITECTURES,
        "Evaluation of competing reasonable architectures or trade-offs.",
    ),
)

AUTO_PATTERNS: tuple[tuple[re.Pattern[str], DecisionCategory, str], ...] = (
    (
        re.compile(r"(?i)\b(refactor\s+internals?|extract\s+helper|rename\s+local|inline\s+method|clean\s+up\s+function|tidy\s+code|code\s+cleanup|format\s+code|pep8|lint\s+fix)\b"),
        DecisionCategory.ORDINARY_REFACTOR,
        "Ordinary internal refactor without changing public contracts.",
    ),
    (
        re.compile(r"(?i)\b(add\s+tests?|unit\s+tests?|test\s+coverage|mock\s+fixture|parameterize\s+test|fix\s+broken\s+test|add\s+assertions?|pytest)\b"),
        DecisionCategory.TESTS_AND_FIXTURES,
        "Adding or fixing unit/integration tests and fixtures.",
    ),
    (
        re.compile(r"(?i)\b(fix\s+(?:local\s+)?bug|fix\s+typo|off-by-one|null\s+check|bounds\s+check|handle\s+edge\s+case|fix\s+exception|handle\s+none|resolve\s+keyerror)\b"),
        DecisionCategory.LOCAL_BUG_FIX,
        "Local bug fix within existing function/method bounds.",
    ),
    (
        re.compile(r"(?i)\b(follow\s+existing\s+pattern|conform\s+to\s+existing|in-place\s+update|add\s+field\s+to\s+contract|implement\s+method\s+in\s+existing|extend\s+existing\s+class)\b"),
        DecisionCategory.EXISTING_ARCHITECTURE,
        "Implementation conforms directly to existing architecture patterns.",
    ),
    (
        re.compile(r"(?i)\b(minimal\s+change|reversible\s+change|docstring|comment\s+update|typing\s+annotation|import\s+cleanup|type\s+hint)\b"),
        DecisionCategory.MINIMAL_REVERSIBLE_CHANGE,
        "Minimal reversible change such as docstrings, type hints, or comments.",
    ),
)

SENSITIVE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(^|/|\\)(?:\.env|\.secrets|credentials|private_key|id_rsa|master_key|broker_auth|live_trade)"),
    re.compile(r"(?i)(?:\.pem|\.key|\.p12|\.pfx)$"),
)

TEST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(?:^|[/\\])(?:test_[^/\\]+\.py|[^/\\]+_test\.py|tests[/\\]?.+)$"),
)


def redact_sensitive_text(val: str) -> str:
    """Scrub sensitive credential-like patterns from text."""
    if not isinstance(val, str):
        return val
    scrubbed = val
    for pat in CREDENTIAL_PATTERNS:
        scrubbed = pat.sub("[REDACTED_CREDENTIAL]", scrubbed)
    return scrubbed


def sanitize_context_value(val: Any) -> Any:
    """Recursively sanitize context structures, removing/redacting credentials."""
    if isinstance(val, str):
        return redact_sensitive_text(val)
    if isinstance(val, dict):
        sanitized: dict[str, Any] = {}
        for k, v in val.items():
            safe_k = redact_sensitive_text(str(k))
            sanitized[safe_k] = sanitize_context_value(v)
        return sanitized
    if isinstance(val, (list, tuple, set)):
        return [sanitize_context_value(item) for item in val]
    return val


@dataclass
class DecisionRecord:
    """Structured, serializable, audit-ready record of an autonomous decision."""

    decision_id: str
    tier: DecisionTier
    category: str
    rationale: str
    assumptions: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    risk_class: RiskClass = RiskClass.CODE_CHANGES
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if isinstance(self.tier, str):
            self.tier = DecisionTier.from_value(self.tier)
        if isinstance(self.risk_class, str):
            self.risk_class = RiskClass.from_value(self.risk_class)
        self.created_at = _format_timestamp(self.created_at) or _utc_now_iso()
        self.validate()

    @property
    def requires_human(self) -> bool:
        """Return True if this decision requires explicit human intervention."""
        return self.tier == DecisionTier.HUMAN_DECISION_REQUIRED

    @property
    def is_autonomous(self) -> bool:
        """Return True if decision can proceed autonomously (AUTO or CODEX)."""
        return self.tier in (DecisionTier.AUTO_DECIDE, DecisionTier.CODEX_DECIDE)

    def validate(self) -> None:
        """Validate structure and ensure no sensitive credentials exist."""
        if not self.decision_id or not self.decision_id.strip():
            raise ValueError("DecisionRecord 'decision_id' cannot be empty.")
        if not self.category or not self.category.strip():
            raise ValueError("DecisionRecord 'category' cannot be empty.")
        if not self.rationale or not self.rationale.strip():
            raise ValueError("DecisionRecord 'rationale' cannot be empty.")
        if not isinstance(self.assumptions, (list, tuple)):
            raise ValueError("DecisionRecord 'assumptions' must be a list of strings.")
        if not isinstance(self.context, dict):
            raise ValueError("DecisionRecord 'context' must be a dictionary.")

        # Credential safety check
        validate_no_credentials(self.rationale, "rationale")
        validate_no_credentials(self.assumptions, "assumptions")
        validate_no_credentials(self.context, "context")

    def to_dict(self) -> dict[str, Any]:
        """Convert to a clean JSON-serializable dictionary."""
        return {
            "decision_id": self.decision_id,
            "tier": self.tier.value,
            "category": self.category,
            "rationale": self.rationale,
            "assumptions": list(self.assumptions),
            "context": sanitize_context_value(self.context),
            "risk_class": self.risk_class.value,
            "requires_human": self.requires_human,
            "is_autonomous": self.is_autonomous,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        """Reconstruct a DecisionRecord from a dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict for DecisionRecord, got {type(data).__name__}")
        return cls(
            decision_id=str(data.get("decision_id", "")),
            tier=DecisionTier.from_value(data.get("tier", DecisionTier.CODEX_DECIDE)),
            category=str(data.get("category", "")),
            rationale=str(data.get("rationale", "")),
            assumptions=list(data.get("assumptions", [])),
            context=dict(data.get("context", {})),
            risk_class=RiskClass.from_value(data.get("risk_class", RiskClass.CODE_CHANGES)),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialize DecisionRecord to a JSON string."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> DecisionRecord:
        """Deserialize DecisionRecord from a JSON string."""
        return cls.from_dict(json.loads(json_str))


def _generate_decision_id() -> str:
    """Generate a deterministic-prefixed random decision id."""
    return f"dec-{uuid.uuid4().hex[:12]}"


def evaluate_decision_policy(
    intent: str,
    modified_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    risk_class: RiskClass | str = RiskClass.CODE_CHANGES,
    repair_attempts: int = 0,
    max_repair_attempts: int = 3,
    context: dict[str, Any] | None = None,
    explicit_tier: DecisionTier | str | None = None,
    assumptions: list[str] | None = None,
    decision_id: str | None = None,
) -> DecisionRecord:
    """Evaluate autonomous decision policy as a pure function.

    Args:
        intent: Natural language task description or decision prompt.
        modified_paths: Files or directories touched by the task.
        risk_class: Associated task risk classification.
        repair_attempts: Number of repair attempts already made.
        max_repair_attempts: Max allowed repair attempts before requiring human.
        context: Optional contextual metadata.
        explicit_tier: Optional explicit tier override if supplied.
        assumptions: Optional caller-provided assumptions.
        decision_id: Optional explicit decision id.

    Returns:
        Structured DecisionRecord with tier, category, rationale, assumptions,
        and sanitized context.
    """
    norm_risk = RiskClass.from_value(risk_class) if not isinstance(risk_class, RiskClass) else risk_class
    norm_paths = normalize_paths(modified_paths)
    safe_context = sanitize_context_value(context or {})
    recorded_assumptions: list[str] = list(assumptions or [])
    dec_id = decision_id or _generate_decision_id()

    # 1. Check explicit tier override if provided
    if explicit_tier is not None:
        target_tier = DecisionTier.from_value(explicit_tier)
        cat = DecisionCategory.EXPLICIT_HUMAN_REQUEST.value if target_tier == DecisionTier.HUMAN_DECISION_REQUIRED else "EXPLICIT_OVERRIDE"
        rationale = f"Explicit decision tier override: {target_tier.value}"
        if not recorded_assumptions:
            recorded_assumptions.append("Explicit authority tier requested by supervisor or caller.")
        return DecisionRecord(
            decision_id=dec_id,
            tier=target_tier,
            category=cat,
            rationale=rationale,
            assumptions=recorded_assumptions,
            context=safe_context,
            risk_class=norm_risk,
        )

    # 2. Check Repeated Unresolved Repair Exhaustion (HUMAN_DECISION_REQUIRED)
    if repair_attempts >= max_repair_attempts:
        rationale = f"Repeated unresolved repair attempts ({repair_attempts}/{max_repair_attempts}) exhausted."
        recorded_assumptions.append("Automated self-repair loop exceeded max threshold; human guidance needed.")
        return DecisionRecord(
            decision_id=dec_id,
            tier=DecisionTier.HUMAN_DECISION_REQUIRED,
            category=DecisionCategory.REPEATED_UNRESOLVED_REPAIR.value,
            rationale=rationale,
            assumptions=recorded_assumptions,
            context=safe_context,
            risk_class=norm_risk,
        )

    # 3. Check Sensitive / Credential File Paths (HUMAN_DECISION_REQUIRED)
    for p in norm_paths:
        for pat in SENSITIVE_PATH_PATTERNS:
            if pat.search(p):
                rationale = f"Modified path '{p}' intersects security/credential boundary."
                recorded_assumptions.append("Path touches sensitive credential or security configuration files.")
                return DecisionRecord(
                    decision_id=dec_id,
                    tier=DecisionTier.HUMAN_DECISION_REQUIRED,
                    category=DecisionCategory.CREDENTIAL_SECURITY_BOUNDARY.value,
                    rationale=rationale,
                    assumptions=recorded_assumptions,
                    context=safe_context,
                    risk_class=norm_risk,
                )

    # 4. Check Human Keyword / Intent Patterns (HUMAN_DECISION_REQUIRED)
    for pat, cat, reason in HUMAN_PATTERNS:
        if pat.search(intent):
            recorded_assumptions.append(f"Requires human authorization due to high-risk condition: {cat.value}")
            return DecisionRecord(
                decision_id=dec_id,
                tier=DecisionTier.HUMAN_DECISION_REQUIRED,
                category=cat.value,
                rationale=reason,
                assumptions=recorded_assumptions,
                context=safe_context,
                risk_class=norm_risk,
            )

    # 5. Check Production Risk Class for Destructive Operations (HUMAN_DECISION_REQUIRED)
    if norm_risk in (RiskClass.PRODUCTION, RiskClass.DESTRUCTIVE) and any(
        kw in intent.lower() for kw in ("drop", "delete", "destroy", "purge", "wipe", "truncate", "migrate")
    ):
        rationale = f"Destructive operation with risk class {norm_risk.value} requires human confirmation."
        recorded_assumptions.append("Destructive production operation requires explicit human confirmation.")
        return DecisionRecord(
            decision_id=dec_id,
            tier=DecisionTier.HUMAN_DECISION_REQUIRED,
            category=DecisionCategory.IRREVERSIBLE_DATA_OR_MIGRATION.value,
            rationale=rationale,
            assumptions=recorded_assumptions,
            context=safe_context,
            risk_class=norm_risk,
        )

    # 6. Check Codex Patterns (CODEX_DECIDE)
    for pat, cat, reason in CODEX_PATTERNS:
        if pat.search(intent):
            if not recorded_assumptions:
                recorded_assumptions.append("Codex autonomously decides architectural trade-offs; preserves backward compatibility.")
            return DecisionRecord(
                decision_id=dec_id,
                tier=DecisionTier.CODEX_DECIDE,
                category=cat.value,
                rationale=reason,
                assumptions=recorded_assumptions,
                context=safe_context,
                risk_class=norm_risk,
            )

    # 7. Check Auto Patterns (AUTO_DECIDE)
    for pat, cat, reason in AUTO_PATTERNS:
        if pat.search(intent):
            if not recorded_assumptions:
                recorded_assumptions.append("Change is localized, reversible, and within existing architecture boundaries.")
            return DecisionRecord(
                decision_id=dec_id,
                tier=DecisionTier.AUTO_DECIDE,
                category=cat.value,
                rationale=reason,
                assumptions=recorded_assumptions,
                context=safe_context,
                risk_class=norm_risk,
            )

    # 8. Check if only test paths are modified (AUTO_DECIDE)
    if norm_paths and all(any(tpat.search(p) for tpat in TEST_PATH_PATTERNS) for p in norm_paths):
        if not recorded_assumptions:
            recorded_assumptions.append("Modifications are confined strictly to test suite files.")
        return DecisionRecord(
            decision_id=dec_id,
            tier=DecisionTier.AUTO_DECIDE,
            category=DecisionCategory.TESTS_AND_FIXTURES.value,
            rationale="All modified paths are isolated unit or integration test files.",
            assumptions=recorded_assumptions,
            context=safe_context,
            risk_class=norm_risk,
        )

    # 9. Default behavior: CODEX_DECIDE (Do not ask human for ordinary uncertainty)
    if not recorded_assumptions:
        recorded_assumptions.append("Defaulting to Codex autonomous decision for general development uncertainty.")
    return DecisionRecord(
        decision_id=dec_id,
        tier=DecisionTier.CODEX_DECIDE,
        category=DecisionCategory.GENERAL_UNCERTAINTY.value,
        rationale="Defaulting to CODEX_DECIDE for general development uncertainty without human intervention.",
        assumptions=recorded_assumptions,
        context=safe_context,
        risk_class=norm_risk,
    )
