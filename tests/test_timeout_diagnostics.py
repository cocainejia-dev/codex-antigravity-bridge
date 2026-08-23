from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_agy_bridge.timeout_diagnostics import (
    diagnose_timeout,
    evaluate_timeout_diagnostics,
)


def test_connect_dead_no_remote_progress_is_safe_to_retry():
    d = diagnose_timeout("CONNECT_TIMEOUT", "NO", "NO")
    assert d["retry_recommended"] == "YES"
    assert d["reconciliation_required"] == "NO"


def test_remote_timeout_requires_reconciliation_and_duplicate_guard():
    d = diagnose_timeout("REMOTE_EXECUTION_TIMEOUT", "YES", "NO")
    assert d["retry_recommended"] == "RECONCILE_FIRST"
    assert d["quota_duplicate_risk"] == "YES"


def test_local_timeout_alive_never_retries():
    d = diagnose_timeout("LOCAL_SUPERVISION_TIMEOUT", "UNKNOWN", "YES")
    assert d["retry_recommended"] == "NO"
    assert d["reconciliation_required"] == "YES"


def test_diff_zero_does_not_claim_no_progress():
    d = diagnose_timeout("REMOTE_EXECUTION_TIMEOUT", "NO", "NO", diff_zero=True)
    assert d["remote_progress_evidence"] == "UNKNOWN"
    assert d["retry_recommended"] == "RECONCILE_FIRST"


def test_insufficient_evidence_does_not_auto_retry():
    d = evaluate_timeout_diagnostics(error_text="remote timeout", worker_alive=False, diff_zero=True)
    assert d.remote_progress_evidence == "UNKNOWN"
    assert d.retry_recommended == "RECONCILE_FIRST"


def test_guidance_is_secret_safe():
    d = evaluate_timeout_diagnostics(error_text="token=super-secret cookie=abc")
    rendered = d.to_json()
    assert "super-secret" not in rendered
    assert "cookie=abc" not in rendered


def test_agy_print_timeout_reconcile_first():
    d = diagnose_timeout("AGY_PRINT_TIMEOUT")
    assert d["timeout_classification"] == "AGY_PRINT_TIMEOUT"
    assert d["retry_recommended"] == "RECONCILE_FIRST"
    assert d["reconciliation_required"] == "YES"
    assert d["quota_duplicate_risk"] == "UNKNOWN"
    assert "print-mode" in d["guidance"].lower() or "print" in d["guidance"].lower()


def test_evaluate_agy_print_timeout_from_error_text():
    d = evaluate_timeout_diagnostics(error_text="timeout waiting for response")
    assert d.timeout_classification == "AGY_PRINT_TIMEOUT"
    assert d.retry_recommended == "RECONCILE_FIRST"
    assert d.reconciliation_required == "YES"
