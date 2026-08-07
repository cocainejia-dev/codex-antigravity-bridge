"""Validate the files that make the repository's skill distributable."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "agy-supervisor" / "SKILL.md"
METADATA = ROOT / "skills" / "agy-supervisor" / "agents" / "openai.yaml"


def main() -> int:
    if not SKILL.is_file():
        raise SystemExit(f"missing skill file: {SKILL}")
    if not METADATA.is_file():
        raise SystemExit(f"missing skill metadata: {METADATA}")

    skill = SKILL.read_text(encoding="utf-8")
    metadata = METADATA.read_text(encoding="utf-8")
    required = (
        "name: agy-supervisor",
        "description: Use when",
        "agy_ask",
        "agy_start",
        "agy_status",
        "Normal mode",
        "Supervisor mode",
        "docs/agy-plans",
        "git worktree",
        "READY_FOR_AGY",
        "multi-page",
        "dangerously_skip_permissions=false",
        "three",
    )
    forbidden = ("TODO", "TBD", "client_secret", "refresh_token", "access_token")

    if not skill.startswith("---\n") or "\n---\n" not in skill[4:]:
        raise SystemExit("skill frontmatter is missing or malformed")
    missing = [value for value in required if value not in skill]
    if missing:
        raise SystemExit(f"skill is missing required content: {', '.join(missing)}")
    present_forbidden = [value for value in forbidden if value in skill or value in metadata]
    if present_forbidden:
        raise SystemExit(f"skill contains forbidden credential or placeholder text: {', '.join(present_forbidden)}")

    for key in ("display_name:", "short_description:", "default_prompt:"):
        if key not in metadata:
            raise SystemExit(f"skill metadata is missing {key}")

    print("skill validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
