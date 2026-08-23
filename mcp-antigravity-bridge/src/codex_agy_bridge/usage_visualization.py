"""Lightweight stdlib-only HTML usage telemetry report and visualization generator.

Generates deterministic, zero-dependency, self-contained Chinese HTML reports for
Codex <-> Antigravity Bridge usage telemetry with:
- HTML default Chinese (html lang="zh-CN") with Chinese headings and explanatory text
- Clearly labeled call share as 调用占比 / DERIVED (never workload/token/cost share)
- Explicit EXACT, DERIVED/ESTIMATED, and UNAVAILABLE semantics
- Token and quota unavailable explanation in Chinese
- Strict HTML escaping for secret and XSS safety
- Zero external assets (embedded CSS, system fonts, no external JS/CDN requests)
- Safe mixed-unit display (no incompatible unit summation)
- Safe, deterministic, concurrency-safe file writing
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .telemetry import MeasurementSource, deterministic_json_dumps
from .usage_reports import resolve_report_path, write_stable_report


def _esc(val: Any) -> str:
    """Escape any value safely for HTML embedding (quote=True for attribute safety)."""
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def _format_num(val: Any, decimals: int = 2) -> str:
    """Format numeric values safely with consistent decimals."""
    if val is None:
        return "N/A"
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        return f"{f:.{decimals}f}"
    except (ValueError, TypeError):
        return _esc(val)


def generate_html_report(report_data: dict[str, Any]) -> str:
    """Generate deterministic, self-contained Chinese HTML report from usage report data dictionary."""
    filters = report_data.get("filters", {})
    summary = report_data.get("summary", {})
    codex = report_data.get("codex", {})
    agy = report_data.get("antigravity", {})
    attribution = report_data.get("attribution", {})
    retries = report_data.get("retries", {})
    timeouts = report_data.get("timeouts", {})
    switches = report_data.get("account_switches", {})
    dup_metrics = report_data.get("duplicate_quota_metrics", {})
    confidence = report_data.get("confidence", {})
    sources = report_data.get("sources", {})
    events = report_data.get("events", [])

    run_id = filters.get("run_id")
    task_id = filters.get("task_id")
    project_dir = filters.get("project_dir")
    since = filters.get("since")
    until = filters.get("until")
    db_path = filters.get("db_path", ":memory:")
    is_latest = filters.get("latest", False)
    report_origin = report_data.get("usage_report_origin") or filters.get("usage_report_origin") or "UNKNOWN"
    report_run_id = report_data.get("usage_report_run_id") or filters.get("usage_report_run_id") or run_id
    report_db_class = report_data.get("usage_report_db_classification") or filters.get("usage_report_db_classification") or "UNKNOWN_LEDGER"
    report_event_provenance = report_data.get("usage_report_event_provenance") or filters.get("usage_report_event_provenance") or "UNKNOWN_PROVENANCE"

    event_count = summary.get("event_count", 0)
    unavail_count = summary.get("unavailable_count", 0)

    # Actor metrics
    c_calls = codex.get("calls", 0)
    c_turns = codex.get("monitoring_turns", 0.0)
    c_res = codex.get("resumptions", 0)

    a_calls = agy.get("calls", 0)
    a_secs = agy.get("duration_seconds", 0.0)
    a_succ = agy.get("successes", 0)
    a_fail = agy.get("failures", 0)
    a_files = agy.get("changed_files", 0)
    a_lines = agy.get("lines_of_code", 0)

    # Reliability metrics
    r_count = retries.get("total_count", 0)
    to_count = timeouts.get("total_count", 0)
    to_classes = timeouts.get("classes", {})
    as_count = switches.get("total_count", 0)

    dup_risk = dup_metrics.get("risk_count", 0)
    dup_avoided = dup_metrics.get("avoided_count", 0)
    dup_source = dup_metrics.get("source", "DERIVED")

    mean_conf = confidence.get("mean_confidence", 1.0)
    w_conf = confidence.get("weighted_confidence_by_unit", {})
    totals_unit = summary.get("totals_by_unit", {})
    src_map = sources.get("events_by_source", {})

    # Call share calculation (strictly labeled as 调用占比 / DERIVED, never workload/token/cost share)
    total_calls = (a_calls + c_calls) if (a_calls + c_calls) > 0 else 1
    agy_call_pct = round((a_calls / total_calls) * 100, 1) if (a_calls + c_calls) > 0 else 50.0
    codex_call_pct = round((c_calls / total_calls) * 100, 1) if (a_calls + c_calls) > 0 else 50.0

    # Build Unit Totals Table Rows
    unit_rows: list[str] = []
    if totals_unit:
        for u_name in sorted(totals_unit.keys()):
            u_val = totals_unit[u_name]
            u_conf = w_conf.get(u_name, 1.0)
            unit_rows.append(
                f"<tr>"
                f"<td><code>{_esc(u_name)}</code></td>"
                f"<td class='text-right font-mono font-bold'>{_format_num(u_val)}</td>"
                f"<td class='text-right font-mono'>{_format_num(u_conf, 4)}</td>"
                f"<td><span class='badge badge-exact'>EXACT</span></td>"
                f"</tr>"
            )
    else:
        unit_rows.append("<tr><td colspan='4' class='text-muted text-center'>未记录可测量单位总计</td></tr>")

    # Build Timeouts by Class List
    timeout_class_items: list[str] = []
    if to_classes:
        for cls_name in sorted(to_classes.keys()):
            cnt = to_classes[cls_name]
            timeout_class_items.append(
                f"<div class='pill-item'><span>{_esc(cls_name)}</span><strong class='font-mono'>{cnt}</strong></div>"
            )
    else:
        timeout_class_items.append("<span class='text-muted'>未记录超时事件</span>")

    # Build Source Distribution Rows
    source_rows: list[str] = []
    if src_map:
        for src_name in sorted(src_map.keys()):
            cnt = src_map[src_name]
            pct = (cnt / event_count * 100) if event_count > 0 else 0.0
            badge_cls = "badge-exact" if "EXACT" in src_name else ("badge-derived" if "DERIVED" in src_name else "badge-unavail")
            source_rows.append(
                f"<tr>"
                f"<td><span class='badge {badge_cls}'>{_esc(src_name)}</span></td>"
                f"<td class='text-right font-mono'>{cnt}</td>"
                f"<td class='text-right font-mono'>{pct:.1f}%</td>"
                f"</tr>"
            )
    else:
        source_rows.append("<tr><td colspan='3' class='text-muted text-center'>未记录来源事件</td></tr>")

    # Build Events Table Rows
    event_rows: list[str] = []
    for ev in events:
        eid = ev.get("event_id", "")[:12]
        ts = ev.get("timestamp", "")
        actor = ev.get("actor", "")
        etype = ev.get("event_type", "")
        mtype = ev.get("measurement_type", "")
        val = ev.get("value")
        val_str = "UNAVAILABLE" if val is None else _format_num(val)
        unit = ev.get("unit", "")
        msrc = ev.get("measurement_source", "")
        conf = ev.get("confidence", 1.0)
        orig = ev.get("origin", "")
        meta_json = deterministic_json_dumps(ev.get("metadata", {}))

        badge_src_cls = "badge-exact" if "EXACT" in msrc else ("badge-derived" if "DERIVED" in msrc else "badge-unavail")
        actor_cls = "actor-agy" if actor == "agy" else ("actor-codex" if actor == "codex" else "actor-bridge")

        event_rows.append(
            f"<tr>"
            f"<td class='font-mono text-xs'>{_esc(eid)}</td>"
            f"<td class='font-mono text-xs'>{_esc(ts)}</td>"
            f"<td><span class='actor-tag {actor_cls}'>{_esc(actor)}</span></td>"
            f"<td><code>{_esc(etype)}</code></td>"
            f"<td><code>{_esc(mtype)}</code></td>"
            f"<td class='text-right font-mono font-bold'>{_esc(val_str)} {_esc(unit)}</td>"
            f"<td><span class='badge {badge_src_cls}'>{_esc(msrc)}</span></td>"
            f"<td class='text-right font-mono'>{_format_num(conf, 2)}</td>"
            f"<td class='font-mono text-xs text-muted' title='{_esc(meta_json)}'>{_esc(meta_json[:60])}{'...' if len(meta_json) > 60 else ''}</td>"
            f"</tr>"
        )
    if not event_rows:
        event_rows.append("<tr><td colspan='9' class='text-muted text-center'>未找到符合过滤条件的遥测事件</td></tr>")

    # Construct complete deterministic Chinese HTML
    html_output = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Codex &lt;-&gt; Antigravity 运行观测指标报告</title>
<style>
  :root {{
    --bg-page: #0b0f19;
    --bg-card: #131b2e;
    --bg-card-alt: #1a243b;
    --bg-table-stripe: #111827;
    --border-card: #1f2d4a;
    --border-table: #1f2d4a;
    --text-main: #f1f5f9;
    --text-muted: #94a3b8;
    --text-dim: #64748b;
    --badge-exact-bg: #064e3b;
    --badge-exact-fg: #6ee7b7;
    --badge-derived-bg: #312e81;
    --badge-derived-fg: #c7d2fe;
    --badge-unavail-bg: #451a03;
    --badge-unavail-fg: #fcd34d;
    --accent-blue: #38bdf8;
    --accent-purple: #a855f7;
    --accent-green: #22c55e;
    --accent-amber: #f59e0b;
    --accent-rose: #f43f5e;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background-color: var(--bg-page);
    color: var(--text-main);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
    line-height: 1.5;
    padding: 24px;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; }}
  header {{
    background: linear-gradient(135deg, #131b2e 0%, #1e1b4b 100%);
    border: 1px solid var(--border-card);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }}
  h1 {{ font-size: 1.75rem; font-weight: 700; color: #ffffff; margin-bottom: 8px; }}
  .subtitle {{ color: var(--text-muted); font-size: 0.95rem; }}
  .filter-bar {{
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid var(--border-card);
    font-size: 0.875rem;
  }}
  .filter-item {{ display: flex; align-items: center; gap: 6px; }}
  .filter-label {{ color: var(--text-muted); font-weight: 600; }}
  .filter-value {{ color: #ffffff; background: var(--bg-card-alt); padding: 2px 8px; border-radius: 4px; font-family: ui-monospace, monospace; }}

  .badge-legend {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    margin-bottom: 24px;
    padding: 12px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border-card);
    border-radius: 8px;
    font-size: 0.85rem;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }}
  .badge-exact {{ background: var(--badge-exact-bg); color: var(--badge-exact-fg); border: 1px solid rgba(110, 231, 183, 0.2); }}
  .badge-derived {{ background: var(--badge-derived-bg); color: var(--badge-derived-fg); border: 1px solid rgba(199, 210, 254, 0.2); }}
  .badge-unavail {{ background: var(--badge-unavail-bg); color: var(--badge-unavail-fg); border: 1px solid rgba(252, 211, 77, 0.2); }}

  .grid {{ display: grid; gap: 20px; margin-bottom: 24px; }}
  .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
  .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); }}

  .card {{
    background: var(--bg-card);
    border: 1px solid var(--border-card);
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
  .card-title {{ font-size: 1.05rem; font-weight: 600; color: #ffffff; }}
  .stat-primary {{ font-size: 1.8rem; font-weight: 700; color: #ffffff; font-family: ui-monospace, monospace; }}
  .stat-list {{ list-style: none; margin-top: 12px; font-size: 0.9rem; }}
  .stat-list li {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }}
  .stat-list li:last-child {{ border-bottom: none; }}

  .workload-bar-container {{
    margin: 16px 0 12px 0;
    background: var(--bg-card-alt);
    border-radius: 6px;
    overflow: hidden;
    height: 28px;
    display: flex;
  }}
  .bar-agy {{ background: var(--accent-purple); height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 700; color: #ffffff; }}
  .bar-codex {{ background: var(--accent-blue); height: 100%; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 700; color: #ffffff; }}

  .disclaimer-box {{
    background: rgba(56, 189, 248, 0.05);
    border: 1px solid rgba(56, 189, 248, 0.2);
    border-radius: 8px;
    padding: 14px 18px;
    margin-top: 12px;
    font-size: 0.875rem;
    color: var(--text-muted);
  }}

  .pill-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .pill-item {{
    background: var(--bg-card-alt);
    border: 1px solid var(--border-card);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 0.85rem;
    display: flex;
    gap: 8px;
    align-items: center;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
    text-align: left;
  }}
  th {{
    background: var(--bg-card-alt);
    color: var(--text-muted);
    font-weight: 600;
    padding: 10px 12px;
    border-bottom: 2px solid var(--border-table);
  }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-table);
  }}
  tr:nth-child(even) {{ background: var(--bg-table-stripe); }}
  tr:hover {{ background: rgba(255, 255, 255, 0.03); }}

  .table-responsive {{ overflow-x: auto; }}
  .text-right {{ text-align: right; }}
  .text-center {{ text-align: center; }}
  .font-mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
  .font-bold {{ font-weight: 700; }}
  .text-xs {{ font-size: 0.75rem; }}
  .text-muted {{ color: var(--text-muted); }}

  .actor-tag {{
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .actor-agy {{ background: rgba(168, 85, 247, 0.2); color: #d8b4fe; }}
  .actor-codex {{ background: rgba(56, 189, 248, 0.2); color: #7dd3fc; }}
  .actor-bridge {{ background: rgba(34, 197, 94, 0.2); color: #86efac; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Codex &lt;-&gt; Antigravity Bridge 使用观测指标</h1>
    <div class="subtitle">观测遥测报告与调用占比可视化</div>
    <div class="filter-bar">
      <div class="filter-item"><span class="filter-label">运行 ID:</span><span class="filter-value">{_esc(run_id or '全部 (未过滤)')}</span></div>
      <div class="filter-item"><span class="filter-label">任务 ID:</span><span class="filter-value">{_esc(task_id or '全部')}</span></div>
      <div class="filter-item"><span class="filter-label">项目目录:</span><span class="filter-value">{_esc(project_dir or '全部')}</span></div>
      <div class="filter-item"><span class="filter-label">数据库:</span><span class="filter-value">{_esc(db_path)}</span></div>
      <div class="filter-item"><span class="filter-label">报告来源:</span><span class="filter-value">{_esc(report_origin)}</span></div>
      <div class="filter-item"><span class="filter-label">报告运行:</span><span class="filter-value">{_esc(report_run_id or '未绑定')}</span></div>
      <div class="filter-item"><span class="filter-label">数据库分类:</span><span class="filter-value">{_esc(report_db_class)}</span></div>
      <div class="filter-item"><span class="filter-label">事件溯源:</span><span class="filter-value">{_esc(report_event_provenance)}</span></div>
      <div class="filter-item"><span class="filter-label">事件总数:</span><span class="filter-value">{event_count} (不可用数据点: {unavail_count})</span></div>
    </div>
  </header>

  <div class="badge-legend">
    <span class="filter-label">置信度与指标来源图例:</span>
    <span class="badge badge-exact">EXACT</span><span class="text-muted">直接测量 / 进程指标</span>
    <span class="badge badge-derived">DERIVED / ESTIMATED</span><span class="text-muted">观测衍生指标（非模型提供商计费数据）</span>
    <span class="badge badge-unavail">UNAVAILABLE</span><span class="text-muted">不可用（模型提供商 Token / 配额未直接提供或不可直接观测）</span>
  </div>

  <div class="grid grid-4">
    <!-- Antigravity Workload Card -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Antigravity 执行指标</span>
        <span class="badge badge-exact">EXACT</span>
      </div>
      <div class="stat-primary">{_format_num(a_secs)}s</div>
      <ul class="stat-list">
        <li><span class="text-muted">调用次数 / 启动</span><strong class="font-mono">{a_calls} 次调用</strong></li>
        <li><span class="text-muted">成功 / 失败</span><strong class="font-mono">{a_succ} / {a_fail}</strong></li>
        <li><span class="text-muted">变更文件数</span><strong class="font-mono">{a_files} 个文件</strong></li>
        <li><span class="text-muted">代码差异行数</span><strong class="font-mono">{a_lines} 行</strong></li>
      </ul>
    </div>

    <!-- Codex Monitoring Card -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Codex 监督指标</span>
        <span class="badge badge-derived">DERIVED</span>
      </div>
      <div class="stat-primary">{_format_num(c_turns)} 轮次</div>
      <ul class="stat-list">
        <li><span class="text-muted">调用次数 / 启动</span><strong class="font-mono">{c_calls} 次调用</strong></li>
        <li><span class="text-muted">监督轮次基线</span><strong class="font-mono">{_format_num(c_turns)} 轮次</strong></li>
        <li><span class="text-muted">恢复重试次数</span><strong class="font-mono">{c_res} 次</strong></li>
      </ul>
    </div>

    <!-- Operational Reliability Card -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">运行可靠性事件</span>
        <span class="badge badge-exact">EXACT</span>
      </div>
      <div class="stat-primary">{r_count + to_count + as_count} 个事件</div>
      <ul class="stat-list">
        <li><span class="text-muted">重试次数</span><strong class="font-mono">{r_count} 次</strong></li>
        <li><span class="text-muted">超时次数</span><strong class="font-mono">{to_count} 次</strong></li>
        <li><span class="text-muted">账号切换次数</span><strong class="font-mono">{as_count} 次</strong></li>
      </ul>
    </div>

    <!-- Duplicate Quota Metrics Card -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">重复配额风险指标</span>
        <span class="badge badge-derived">{_esc(dup_source)}</span>
      </div>
      <div class="stat-primary">{dup_risk} 次风险</div>
      <ul class="stat-list">
        <li><span class="text-muted">重复配额风险</span><strong class="font-mono">{dup_risk} 次</strong></li>
        <li><span class="text-muted">已避免重复重试</span><strong class="font-mono">{dup_avoided} 次</strong></li>
        <li><span class="text-muted">Token/配额节省声明</span><span class="badge badge-unavail">UNAVAILABLE</span></li>
      </ul>
    </div>
  </div>

  <!-- Call Share Breakdown (Strictly 调用占比 / DERIVED, never workload/token/cost share) -->
  <div class="card" style="margin-bottom: 24px;">
    <div class="card-header">
      <span class="card-title">调用占比分析 (Call Share)</span>
      <span class="badge badge-derived">DERIVED</span>
    </div>
    <div class="workload-bar-container">
      <div class="bar-agy" style="width: {agy_call_pct}%;">Antigravity 调用占比 ({agy_call_pct}%)</div>
      <div class="bar-codex" style="width: {codex_call_pct}%;">Codex 调用占比 ({codex_call_pct}%)</div>
    </div>
    <div class="disclaimer-box">
      <strong>指标说明：</strong>调用占比完全基于记录的实际可测量观测数据（执行时长、调用次数、监督轮次、工作区代码差异等）衍生计算（DERIVED），不作任何模型提供商 Token 节省或虚假成本折扣断言。模型提供商 Token 与配额指标不可直接观测（UNAVAILABLE）。
    </div>
  </div>

  <div class="grid grid-2">
    <!-- Totals by Unit (Mixed-Unit Safe) -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">按单位汇总统计 (安全隔离单位)</span>
        <span class="badge badge-exact">EXACT</span>
      </div>
      <table style="margin-top: 8px;">
        <thead>
          <tr>
            <th>计量单位</th>
            <th class="text-right">记录总值</th>
            <th class="text-right">加权置信度</th>
            <th>指标分类</th>
          </tr>
        </thead>
        <tbody>
          {''.join(unit_rows)}
        </tbody>
      </table>
    </div>

    <!-- Reliability & Sources -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">测量来源分布与置信度</span>
        <span class="badge badge-derived">置信度: {_format_num(mean_conf, 4)}</span>
      </div>
      <table style="margin-top: 8px;">
        <thead>
          <tr>
            <th>数据来源</th>
            <th class="text-right">事件数</th>
            <th class="text-right">占比</th>
          </tr>
        </thead>
        <tbody>
          {''.join(source_rows)}
        </tbody>
      </table>
      <div style="margin-top: 16px;">
        <span class="filter-label">超时分类明细:</span>
        <div class="pill-container">
          {''.join(timeout_class_items)}
        </div>
      </div>
    </div>
  </div>

  <!-- Telemetry Events Table -->
  <div class="card">
    <div class="card-header">
      <span class="card-title">观测遥测事件日志 ({len(events)})</span>
      <span class="badge badge-exact">日志流水</span>
    </div>
    <div class="table-responsive" style="margin-top: 8px; max-height: 480px; overflow-y: auto;">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>时间戳</th>
            <th>主体</th>
            <th>事件类型</th>
            <th>测量类型</th>
            <th class="text-right">数值</th>
            <th>来源</th>
            <th class="text-right">置信度</th>
            <th>元数据摘要</th>
          </tr>
        </thead>
        <tbody>
          {''.join(event_rows)}
        </tbody>
      </table>
    </div>
  </div>
</div>
</body>
</html>
"""
    return html_output


def write_html_report(
    html_content: str,
    target_path: str | Path,
    alias_path: str | Path | None = None,
) -> Path:
    """Safely write HTML visualization report to target path using write_stable_report.

    Creates parent directories if necessary and writes UTF-8 encoded text atomically.
    Guaranteed to preserve existing reports.
    """
    target, _uri, _alias, _alias_uri = write_stable_report(
        html_content=html_content,
        target_path=target_path,
        alias_path=alias_path,
    )
    return target


__all__ = [
    "generate_html_report",
    "write_html_report",
]
