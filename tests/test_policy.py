from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import codex_agy_bridge
    pkg_dir = str(SRC / "codex_agy_bridge")
    if pkg_dir not in codex_agy_bridge.__path__:
        codex_agy_bridge.__path__.insert(0, pkg_dir)
except ImportError:
    pass

import pytest

from codex_agy_bridge.contracts import RiskClass
from codex_agy_bridge.policy import (
    DecisionCategory,
    DecisionRecord,
    DecisionTier,
    evaluate_decision_policy,
    redact_sensitive_text,
    sanitize_context_value,
)


def test_decision_tier_enum_values_and_normalization() -> None:
    assert DecisionTier.AUTO_DECIDE.value == "AUTO_DECIDE"
    assert DecisionTier.CODEX_DECIDE.value == "CODEX_DECIDE"
    assert DecisionTier.HUMAN_DECISION_REQUIRED.value == "HUMAN_DECISION_REQUIRED"

    assert DecisionTier.from_value("auto_decide") == DecisionTier.AUTO_DECIDE
    assert DecisionTier.from_value("codex-decide") == DecisionTier.CODEX_DECIDE
    assert DecisionTier.from_value("HUMAN_DECISION_REQUIRED") == DecisionTier.HUMAN_DECISION_REQUIRED

    with pytest.raises(ValueError, match="Unknown decision tier"):
        DecisionTier.from_value("UNKNOWN_TIER")

    with pytest.raises(ValueError, match="Invalid decision tier type"):
        DecisionTier.from_value(123)  # type: ignore[arg-type]


def test_decision_category_enum_values_and_normalization() -> None:
    assert DecisionCategory.from_value("ordinary_refactor") == DecisionCategory.ORDINARY_REFACTOR
    assert DecisionCategory.from_value("real-funds-or-trades") == DecisionCategory.REAL_FUNDS_OR_TRADES
    assert DecisionCategory.from_value("MODULE_BOUNDARY") == DecisionCategory.MODULE_BOUNDARY

    with pytest.raises(ValueError, match="Unknown decision category"):
        DecisionCategory.from_value("NONEXISTENT_CATEGORY")


def test_auto_decide_ordinary_refactor() -> None:
    record = evaluate_decision_policy("Refactor internals and extract helper functions in runner")
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.ORDINARY_REFACTOR.value
    assert record.requires_human is False
    assert record.is_autonomous is True
    assert len(record.assumptions) > 0
    assert "Change is localized, reversible" in record.assumptions[0]


def test_auto_decide_unit_tests_and_fixtures() -> None:
    record = evaluate_decision_policy("Add unit tests and mock fixture for policy evaluation")
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.TESTS_AND_FIXTURES.value
    assert record.is_autonomous is True


def test_auto_decide_local_bug_fix() -> None:
    record = evaluate_decision_policy("Fix local bug and handle None check edge case in parser")
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.LOCAL_BUG_FIX.value
    assert record.is_autonomous is True


def test_auto_decide_existing_architecture() -> None:
    record = evaluate_decision_policy("Follow existing pattern and add field to contract dataclass")
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.EXISTING_ARCHITECTURE.value
    assert record.is_autonomous is True


def test_auto_decide_minimal_reversible_changes() -> None:
    record = evaluate_decision_policy("Minimal change: update docstrings and typing annotations")
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.MINIMAL_REVERSIBLE_CHANGE.value
    assert record.is_autonomous is True


def test_auto_decide_test_paths_only() -> None:
    record = evaluate_decision_policy(
        intent="Update assertions and fixtures",
        modified_paths=["tests/test_foo.py", "tests/test_bar.py"],
    )
    assert record.tier == DecisionTier.AUTO_DECIDE
    assert record.category == DecisionCategory.TESTS_AND_FIXTURES.value


def test_codex_decide_module_boundaries() -> None:
    record = evaluate_decision_policy("Redefine module boundary and restructure packages")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.MODULE_BOUNDARY.value
    assert record.requires_human is False
    assert record.is_autonomous is True
    assert len(record.assumptions) > 0
    assert "Codex autonomously decides architectural trade-offs" in record.assumptions[0]


def test_codex_decide_public_abstractions() -> None:
    record = evaluate_decision_policy("Design public abstraction and interface contract for bridge")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.PUBLIC_ABSTRACTION.value
    assert record.is_autonomous is True


def test_codex_decide_repository_provider_design() -> None:
    record = evaluate_decision_policy("Implement repository pattern and service provider abstraction")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.REPOSITORY_PROVIDER_DESIGN.value
    assert record.is_autonomous is True


def test_codex_decide_cache_and_concurrency_strategy() -> None:
    record = evaluate_decision_policy("Establish caching policy and concurrency model for run controller")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.CACHE_CONCURRENCY_STRATEGY.value
    assert record.is_autonomous is True


def test_codex_decide_competing_architectures() -> None:
    record = evaluate_decision_policy("Evaluate competing architectures and architectural trade-offs for storage")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.COMPETING_ARCHITECTURES.value
    assert record.is_autonomous is True


def test_default_tier_is_codex_decide_not_human() -> None:
    # Ordinary uncertainty / general tasks without human triggers must default to CODEX_DECIDE
    record = evaluate_decision_policy("Implement feature X with some ambiguous edge cases")
    assert record.tier == DecisionTier.CODEX_DECIDE
    assert record.category == DecisionCategory.GENERAL_UNCERTAINTY.value
    assert record.requires_human is False
    assert record.is_autonomous is True
    assert "Defaulting to Codex autonomous decision" in record.assumptions[0]


def test_human_decision_real_funds_and_broker_permissions() -> None:
    record = evaluate_decision_policy("Execute live trading order with real funds and broker credentials")
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.REAL_FUNDS_OR_TRADES.value
    assert record.requires_human is True
    assert record.is_autonomous is False


def test_human_decision_credential_and_security_boundary() -> None:
    record = evaluate_decision_policy("Implement authentication bypass and rotate master key")
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.CREDENTIAL_SECURITY_BOUNDARY.value
    assert record.requires_human is True


def test_human_decision_sensitive_file_path() -> None:
    record = evaluate_decision_policy(
        intent="Update local configurations",
        modified_paths=[".env.production", "src/main.py"],
    )
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.CREDENTIAL_SECURITY_BOUNDARY.value
    assert record.requires_human is True


def test_human_decision_irreversible_data_or_migration() -> None:
    record = evaluate_decision_policy("Run irreversible migration to drop table users and purge production data")
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.IRREVERSIBLE_DATA_OR_MIGRATION.value
    assert record.requires_human is True


def test_human_decision_destructive_production_risk() -> None:
    record = evaluate_decision_policy(
        intent="Wipe database tables for clean state",
        risk_class=RiskClass.PRODUCTION,
    )
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.IRREVERSIBLE_DATA_OR_MIGRATION.value
    assert record.requires_human is True


def test_human_decision_major_product_conflict() -> None:
    record = evaluate_decision_policy("Resolve major product conflict with contradictory user requirements")
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.MAJOR_PRODUCT_CONFLICT.value
    assert record.requires_human is True


def test_human_decision_legal_compliance_risk() -> None:
    record = evaluate_decision_policy("Analyze potential GDPR breach and legal compliance violation")
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.LEGAL_COMPLIANCE_RISK.value
    assert record.requires_human is True


def test_human_decision_repeated_unresolved_repair() -> None:
    record = evaluate_decision_policy(
        intent="Attempt repair on failing tests",
        repair_attempts=3,
        max_repair_attempts=3,
    )
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.category == DecisionCategory.REPEATED_UNRESOLVED_REPAIR.value
    assert record.requires_human is True
    assert "Repeated unresolved repair attempts (3/3) exhausted" in record.rationale


def test_explicit_tier_override() -> None:
    record = evaluate_decision_policy(
        intent="Simple refactor",
        explicit_tier=DecisionTier.HUMAN_DECISION_REQUIRED,
    )
    assert record.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert record.requires_human is True

    record_auto = evaluate_decision_policy(
        intent="Some architectural idea",
        explicit_tier="AUTO_DECIDE",
    )
    assert record_auto.tier == DecisionTier.AUTO_DECIDE
    assert record_auto.is_autonomous is True


def test_decision_record_serialization_roundtrip() -> None:
    record = DecisionRecord(
        decision_id="dec-test-12345",
        tier=DecisionTier.CODEX_DECIDE,
        category="MODULE_BOUNDARY",
        rationale="Cross-module restructuring needed for separation of concerns.",
        assumptions=["Backward compatibility preserved", "No external breaking change"],
        context={"scope": "internal", "affected_count": 3},
        risk_class=RiskClass.CODE_CHANGES,
    )

    d = record.to_dict()
    assert d["decision_id"] == "dec-test-12345"
    assert d["tier"] == "CODEX_DECIDE"
    assert d["requires_human"] is False
    assert d["is_autonomous"] is True

    json_str = record.to_json()
    reconstructed = DecisionRecord.from_json(json_str)

    assert reconstructed.decision_id == record.decision_id
    assert reconstructed.tier == record.tier
    assert reconstructed.category == record.category
    assert reconstructed.rationale == record.rationale
    assert reconstructed.assumptions == record.assumptions
    assert reconstructed.context == record.context
    assert reconstructed.risk_class == record.risk_class


def test_forbidden_credential_persistence_in_decision_record() -> None:
    # Attempting to store raw credentials in DecisionRecord fields must raise ValueError
    with pytest.raises(ValueError, match="Credential-like content detected"):
        DecisionRecord(
            decision_id="dec-leak",
            tier=DecisionTier.CODEX_DECIDE,
            category="TEST",
            rationale="Using ghp_123456789012345678901234567890 to authenticate",
        )

    with pytest.raises(ValueError, match="Credential-like content detected"):
        DecisionRecord(
            decision_id="dec-leak-2",
            tier=DecisionTier.AUTO_DECIDE,
            category="TEST",
            rationale="Clean refactor",
            assumptions=["Using sk-abcdefghijklmnopqrstuvwxyz123456"],
        )

    with pytest.raises(ValueError, match="Credential-like content detected"):
        DecisionRecord(
            decision_id="dec-leak-3",
            tier=DecisionTier.AUTO_DECIDE,
            category="TEST",
            rationale="Clean refactor",
            context={"api_key": "AKIA1234567890ABCDEF"},
        )


def test_credential_sanitization_helpers() -> None:
    raw_text = "Bearer secret_token_123456789012 with ghp_123456789012345678901234"
    scrubbed = redact_sensitive_text(raw_text)
    assert "[REDACTED_CREDENTIAL]" in scrubbed
    assert "ghp_" not in scrubbed

    context = {
        "user": "alice",
        "nested": {
            "token": "sk-12345678901234567890",
            "count": 42,
        },
    }
    sanitized = sanitize_context_value(context)
    assert sanitized["user"] == "alice"
    assert sanitized["nested"]["count"] == 42
    assert "[REDACTED_CREDENTIAL]" in sanitized["nested"]["token"]
