"""Reusable diagnostic policy and secret-safe output helpers for timeout classification and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any

from .agy_runner import classify_agy_error
from .contracts import (
    CREDENTIAL_PATTERNS,
    TimeoutClassification,
)


class ProgressEvidence(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_value(cls, val: Any) -> ProgressEvidence:
        if isinstance(val, cls):
            return val
        if isinstance(val, bool):
            return cls.YES if val else cls.NO
        if isinstance(val, str):
            norm = val.strip().upper()
            if norm in ("YES", "Y", "TRUE", "1"):
                return cls.YES
            if norm in ("NO", "N", "FALSE", "0"):
                return cls.NO
            if norm in ("UNKNOWN", "UNK"):
                return cls.UNKNOWN
        return cls.UNKNOWN


class LivenessStatus(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_value(cls, val: Any) -> LivenessStatus:
        if isinstance(val, cls):
            return val
        if isinstance(val, bool):
            return cls.YES if val else cls.NO
        if isinstance(val, str):
            norm = val.strip().upper()
            if norm in ("YES", "Y", "TRUE", "1"):
                return cls.YES
            if norm in ("NO", "N", "FALSE", "0"):
                return cls.NO
            if norm in ("UNKNOWN", "UNK"):
                return cls.UNKNOWN
        return cls.UNKNOWN


class RetryRecommendation(str, Enum):
    YES = "YES"
    NO = "NO"
    RECONCILE_FIRST = "RECONCILE_FIRST"

    @classmethod
    def from_value(cls, val: Any) -> RetryRecommendation:
        if isinstance(val, cls):
            return val
        if isinstance(val, bool):
            return cls.YES if val else cls.NO
        if isinstance(val, str):
            norm = val.strip().upper().replace("-", "_").replace(" ", "_")
            if norm in ("YES", "Y", "TRUE", "1"):
                return cls.YES
            if norm in ("NO", "N", "FALSE", "0"):
                return cls.NO
            if norm in ("RECONCILE_FIRST", "RECONCILE", "RECONCILEFIRST"):
                return cls.RECONCILE_FIRST
        raise ValueError(f"Unknown retry recommendation: {val!r}")


class ReconciliationStatus(str, Enum):
    YES = "YES"
    NO = "NO"

    @classmethod
    def from_value(cls, val: Any) -> ReconciliationStatus:
        if isinstance(val, cls):
            return val
        if isinstance(val, bool):
            return cls.YES if val else cls.NO
        if isinstance(val, str):
            norm = val.strip().upper()
            if norm in ("YES", "Y", "TRUE", "1"):
                return cls.YES
            if norm in ("NO", "N", "FALSE", "0"):
                return cls.NO
        raise ValueError(f"Unknown reconciliation status: {val!r}")


class QuotaDuplicateRisk(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_value(cls, val: Any) -> QuotaDuplicateRisk:
        if isinstance(val, cls):
            return val
        if isinstance(val, bool):
            return cls.YES if val else cls.NO
        if isinstance(val, str):
            norm = val.strip().upper()
            if norm in ("YES", "Y", "TRUE", "1"):
                return cls.YES
            if norm in ("NO", "N", "FALSE", "0"):
                return cls.NO
            if norm in ("UNKNOWN", "UNK"):
                return cls.UNKNOWN
        return cls.UNKNOWN


_SECRET_KEY_SUBSTRINGS = (
    "token",
    "cookie",
    "secret",
    "password",
    "passwd",
    "auth_header",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "session_token",
)


def redact_sensitive_text(val: str) -> str:
    """Scrub sensitive credentials, tokens, cookies, and secret patterns from text."""
    if not isinstance(val, str):
        return str(val) if val is not None else ""
    scrubbed = val
    for pat in CREDENTIAL_PATTERNS:
        scrubbed = pat.sub("[REDACTED_CREDENTIAL]", scrubbed)
    scrubbed = re.sub(r"(?i)\b(bearer\s+)[a-z0-9_\-\.]{8,}", r"\1[REDACTED]", scrubbed)
    scrubbed = re.sub(r"(?i)\b(cookie:\s*)[^\r\n]+", r"\1[REDACTED]", scrubbed)
    scrubbed = re.sub(r"(?i)\b(oauth_token=)[^\s&'\"]+", r"\1[REDACTED]", scrubbed)
    return scrubbed


def sanitize_diagnostic_details(val: Any) -> Any:
    """Recursively sanitize diagnostic details to guarantee no secrets/tokens are logged."""
    if isinstance(val, str):
        return redact_sensitive_text(val)
    if isinstance(val, dict):
        sanitized: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k)
            k_lower = k_str.lower()
            if any(term in k_lower for term in _SECRET_KEY_SUBSTRINGS):
                sanitized[k_str] = "[REDACTED]"
            else:
                sanitized[k_str] = sanitize_diagnostic_details(v)
        return sanitized
    if isinstance(val, (list, tuple, set)):
        return [sanitize_diagnostic_details(item) for item in val]
    return val


@dataclass
class TimeoutDiagnostic:
    """Structured, secret-safe diagnostic outcome for timeout and execution failures."""

    timeout_classification: str | None
    remote_progress_evidence: str
    worker_alive: str
    retry_recommended: str
    reconciliation_required: str
    quota_duplicate_risk: str
    guidance: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_classification": self.timeout_classification,
            "remote_progress_evidence": self.remote_progress_evidence,
            "worker_alive": self.worker_alive,
            "retry_recommended": self.retry_recommended,
            "reconciliation_required": self.reconciliation_required,
            "quota_duplicate_risk": self.quota_duplicate_risk,
            "guidance": self.guidance,
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeoutDiagnostic:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            timeout_classification=data.get("timeout_classification"),
            remote_progress_evidence=str(data.get("remote_progress_evidence", ProgressEvidence.UNKNOWN.value)),
            worker_alive=str(data.get("worker_alive", LivenessStatus.UNKNOWN.value)),
            retry_recommended=str(data.get("retry_recommended", RetryRecommendation.RECONCILE_FIRST.value)),
            reconciliation_required=str(data.get("reconciliation_required", ReconciliationStatus.YES.value)),
            quota_duplicate_risk=str(data.get("quota_duplicate_risk", QuotaDuplicateRisk.UNKNOWN.value)),
            guidance=str(data.get("guidance", data.get("user_guidance", ""))),
            details=dict(data.get("details", {})),
        )


def diagnose_timeout(
    classification: TimeoutClassification | str | None,
    remote_progress_evidence: str | bool = "UNKNOWN",
    worker_alive: str | bool = "UNKNOWN",
    diff_zero: bool = False,
) -> dict[str, Any]:
    """Pure helper evaluating conservative timeout policy and returning exact UX fields.

    Args:
        classification: Timeout classification name, enum, or None.
        remote_progress_evidence: 'YES', 'NO', 'UNKNOWN', or boolean.
        worker_alive: 'YES', 'NO', 'UNKNOWN', or boolean.
        diff_zero: True if worktree diff was zero. diff=0 must never turn evidence into NO.

    Returns:
        Exact dictionary with keys:
        - timeout_classification
        - remote_progress_evidence
        - worker_alive
        - retry_recommended
        - reconciliation_required
        - quota_duplicate_risk
        - guidance
    """
    # 1. Resolve classification string
    resolved_timeout: str | None = None
    if classification is not None:
        if isinstance(classification, TimeoutClassification):
            resolved_timeout = classification.value
        elif isinstance(classification, str) and classification.strip():
            raw_str = classification.strip()
            try:
                resolved_timeout = TimeoutClassification.from_value(raw_str).value
            except ValueError:
                kind = classify_agy_error(raw_str)
                if kind in (
                    TimeoutClassification.CONNECT_TIMEOUT.value,
                    TimeoutClassification.REMOTE_EXECUTION_TIMEOUT.value,
                    TimeoutClassification.LOCAL_SUPERVISION_TIMEOUT.value,
                    TimeoutClassification.AGY_PRINT_TIMEOUT.value,
                ):
                    resolved_timeout = kind
                elif "PRINT" in raw_str.upper():
                    resolved_timeout = TimeoutClassification.AGY_PRINT_TIMEOUT.value
                elif "CONNECT" in raw_str.upper():
                    resolved_timeout = TimeoutClassification.CONNECT_TIMEOUT.value
                elif "REMOTE" in raw_str.upper():
                    resolved_timeout = TimeoutClassification.REMOTE_EXECUTION_TIMEOUT.value
                elif "SUPERVISION" in raw_str.upper() or "LOCAL" in raw_str.upper():
                    resolved_timeout = TimeoutClassification.LOCAL_SUPERVISION_TIMEOUT.value
                elif raw_str.upper() not in ("NONE", "NULL"):
                    resolved_timeout = raw_str

    # 2. Resolve worker liveness
    norm_worker_alive = LivenessStatus.from_value(worker_alive).value

    # 3. Resolve remote progress evidence
    norm_remote_evidence = ProgressEvidence.from_value(remote_progress_evidence).value

    # Invariant: diff_zero must not turn evidence into NO.
    if diff_zero:
        if norm_remote_evidence == ProgressEvidence.NO.value:
            norm_remote_evidence = ProgressEvidence.UNKNOWN.value

    # 4. Conservative Policy Evaluation
    retry_recommended: str
    reconciliation_required: str
    quota_duplicate_risk: str
    guidance: str

    if resolved_timeout is None:
        retry_recommended = RetryRecommendation.NO.value
        reconciliation_required = ReconciliationStatus.NO.value
        quota_duplicate_risk = QuotaDuplicateRisk.NO.value
        guidance = "No timeout condition detected."
    elif norm_worker_alive == LivenessStatus.YES.value:
        # Invariant: Worker is still alive; never retry directly; duplicate workers forbidden.
        retry_recommended = RetryRecommendation.NO.value
        reconciliation_required = ReconciliationStatus.YES.value
        quota_duplicate_risk = QuotaDuplicateRisk.YES.value
        guidance = (
            "Worker is still active. Do not retry or spawn duplicate workers; "
            "reconcile or observe existing execution."
        )
    elif norm_remote_evidence == ProgressEvidence.YES.value:
        # Any remote evidence YES -> never direct retry; duplicate risk YES.
        retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
        reconciliation_required = ReconciliationStatus.YES.value
        quota_duplicate_risk = QuotaDuplicateRisk.YES.value
        guidance = (
            "Remote execution progress was detected. Reconcile worktree and inspect state "
            "before considering any retry."
        )
    elif resolved_timeout == TimeoutClassification.AGY_PRINT_TIMEOUT.value:
        retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
        reconciliation_required = ReconciliationStatus.YES.value
        quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
        guidance = (
            "AGY print-mode response timed out. Reconcile worktree and inspect existing partial "
            "progress before retrying on the same worktree."
        )
    elif resolved_timeout == TimeoutClassification.CONNECT_TIMEOUT.value:
        if norm_remote_evidence == ProgressEvidence.NO.value and norm_worker_alive == LivenessStatus.NO.value:
            retry_recommended = RetryRecommendation.YES.value
            reconciliation_required = ReconciliationStatus.NO.value
            quota_duplicate_risk = QuotaDuplicateRisk.NO.value
            guidance = (
                "Connection timed out before remote execution began and worker is terminated. "
                "Safe to retry once."
            )
        else:
            retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
            reconciliation_required = ReconciliationStatus.YES.value
            quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
            guidance = (
                "Connection timeout with unverified remote state. Reconcile active state before retry."
            )
    elif resolved_timeout == TimeoutClassification.REMOTE_EXECUTION_TIMEOUT.value:
        retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
        reconciliation_required = ReconciliationStatus.YES.value
        quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
        guidance = (
            "Remote execution timed out. Reconcile worktree and check status before attempting "
            "retry to prevent duplicate operations."
        )
    elif resolved_timeout == TimeoutClassification.LOCAL_SUPERVISION_TIMEOUT.value:
        if norm_worker_alive == LivenessStatus.NO.value:
            if norm_remote_evidence == ProgressEvidence.NO.value:
                retry_recommended = RetryRecommendation.YES.value
                reconciliation_required = ReconciliationStatus.NO.value
                quota_duplicate_risk = QuotaDuplicateRisk.NO.value
                guidance = (
                    "Local supervision timed out with no remote progress and worker terminated. "
                    "Safe to retry once."
                )
            else:
                retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
                reconciliation_required = ReconciliationStatus.YES.value
                quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
                guidance = (
                    "Local supervision timed out and worker terminated with uncertain progress. "
                    "Reconcile worktree and results before retry."
                )
        else:
            retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
            reconciliation_required = ReconciliationStatus.YES.value
            quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
            guidance = (
                "Local supervision timed out and worker status is unknown. Reconcile before retry."
            )
    else:
        retry_recommended = RetryRecommendation.RECONCILE_FIRST.value
        reconciliation_required = ReconciliationStatus.YES.value
        quota_duplicate_risk = QuotaDuplicateRisk.UNKNOWN.value
        guidance = "Timeout occurred. Reconcile state before retrying."

    return {
        "timeout_classification": resolved_timeout,
        "remote_progress_evidence": norm_remote_evidence,
        "worker_alive": norm_worker_alive,
        "retry_recommended": retry_recommended,
        "reconciliation_required": reconciliation_required,
        "quota_duplicate_risk": quota_duplicate_risk,
        "guidance": redact_sensitive_text(guidance),
    }


def evaluate_timeout_diagnostics(
    *,
    timeout_classification: TimeoutClassification | str | None = None,
    error_text: str = "",
    stderr: str = "",
    output_text: str = "",
    worker_alive: str | bool | None = None,
    remote_progress_evidence: str | bool | None = None,
    diff_bytes: int | None = None,
    diff_count: int | None = None,
    has_worktree_changes: bool | None = None,
    git_head_changed: bool | None = None,
    diff_zero: bool = False,
    context: dict[str, Any] | None = None,
) -> TimeoutDiagnostic:
    """Evaluate conservative timeout policy and return structured TimeoutDiagnostic object."""
    # 1. Resolve classification
    resolved_timeout: str | None = None
    if timeout_classification is not None:
        if isinstance(timeout_classification, TimeoutClassification):
            resolved_timeout = timeout_classification.value
        elif isinstance(timeout_classification, str) and timeout_classification.strip():
            raw_str = timeout_classification.strip()
            try:
                resolved_timeout = TimeoutClassification.from_value(raw_str).value
            except ValueError:
                kind = classify_agy_error(raw_str)
                if kind in (
                    TimeoutClassification.CONNECT_TIMEOUT.value,
                    TimeoutClassification.REMOTE_EXECUTION_TIMEOUT.value,
                    TimeoutClassification.LOCAL_SUPERVISION_TIMEOUT.value,
                    TimeoutClassification.AGY_PRINT_TIMEOUT.value,
                ):
                    resolved_timeout = kind

    if resolved_timeout is None:
        combined_candidates = [error_text, stderr, output_text]
        if context and isinstance(context, dict):
            for k in ("error", "last_error", "reason", "suspended_reason", "error_kind"):
                val = context.get(k)
                if isinstance(val, str) and val.strip():
                    combined_candidates.append(val)
        combined_text = "\n".join(c for c in combined_candidates if c)
        if combined_text:
            kind = classify_agy_error(combined_text)
            if kind in (
                TimeoutClassification.CONNECT_TIMEOUT.value,
                TimeoutClassification.REMOTE_EXECUTION_TIMEOUT.value,
                TimeoutClassification.LOCAL_SUPERVISION_TIMEOUT.value,
                TimeoutClassification.AGY_PRINT_TIMEOUT.value,
            ):
                resolved_timeout = kind

    # 2. Worker liveness
    resolved_alive = worker_alive
    if resolved_alive is None:
        if context and isinstance(context, dict) and "is_alive" in context:
            resolved_alive = context["is_alive"]
        elif context and isinstance(context, dict) and "state" in context:
            state_str = str(context["state"]).upper()
            if state_str in ("COMPLETE", "FAILED", "CANCELLED"):
                resolved_alive = "NO"
            elif state_str in ("RUNNING", "QUEUED", "VERIFYING", "REPAIRING", "COMMITTING"):
                resolved_alive = "YES"
            else:
                resolved_alive = "UNKNOWN"
        else:
            resolved_alive = "UNKNOWN"

    # 3. Evidence
    resolved_evidence = remote_progress_evidence
    is_diff_zero = diff_zero or (diff_bytes == 0 and diff_count == 0 and has_worktree_changes is False)
    if resolved_evidence is None:
        has_positive_evidence = (
            git_head_changed is True
            or has_worktree_changes is True
            or (diff_bytes is not None and diff_bytes > 0)
            or (diff_count is not None and diff_count > 0)
        )
        if has_positive_evidence:
            resolved_evidence = "YES"
        elif is_diff_zero:
            # Invariant: diff_zero must not turn evidence into NO
            resolved_evidence = "UNKNOWN"
        elif resolved_timeout == TimeoutClassification.CONNECT_TIMEOUT.value:
            resolved_evidence = "NO"
        else:
            resolved_evidence = "UNKNOWN"

    diag_dict = diagnose_timeout(
        classification=resolved_timeout,
        remote_progress_evidence=resolved_evidence,
        worker_alive=resolved_alive,
        diff_zero=is_diff_zero,
    )

    details_dict: dict[str, Any] = {}
    if context and isinstance(context, dict):
        for k, v in context.items():
            if k not in ("worker_alive", "remote_progress_evidence"):
                details_dict[str(k)] = v
    if error_text:
        details_dict["error_summary"] = redact_sensitive_text(error_text.strip()[:200])

    return TimeoutDiagnostic(
        timeout_classification=diag_dict["timeout_classification"],
        remote_progress_evidence=diag_dict["remote_progress_evidence"],
        worker_alive=diag_dict["worker_alive"],
        retry_recommended=diag_dict["retry_recommended"],
        reconciliation_required=diag_dict["reconciliation_required"],
        quota_duplicate_risk=diag_dict["quota_duplicate_risk"],
        guidance=diag_dict["guidance"],
        details=sanitize_diagnostic_details(details_dict),
    )
