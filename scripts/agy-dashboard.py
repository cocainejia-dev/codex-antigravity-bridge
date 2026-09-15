from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-antigravity-bridge" / "src"))
from codex_agy_bridge.agy_jobs import agy_jobs  # noqa: E402


HTML = """<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>AGY Supervisor</title>
<style>body{font:15px system-ui;margin:0;background:#f3f6fb;color:#172033}.wrap{max-width:1050px;margin:36px auto;padding:0 22px}h1{margin-bottom:6px}.sub{color:#64748b;margin-bottom:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}.card{background:white;border-radius:14px;padding:18px;box-shadow:0 3px 14px #17203312;border-left:6px solid #94a3b8}.running{border-color:#22c55e}.failed{border-color:#ef4444}.completed{border-color:#64748b}.lost{border-color:#f59e0b}.badge{float:right;border-radius:99px;padding:4px 10px;font-size:12px;background:#e2e8f0}.running .badge{background:#dcfce7;color:#166534}.failed .badge,.lost .badge{background:#fee2e2;color:#991b1b}.meta{color:#64748b;font-size:13px;line-height:1.7}.job{font-family:monospace;font-size:12px;color:#475569;word-break:break-all}.empty{background:white;padding:30px;border-radius:14px;text-align:center;color:#64748b}details{margin-top:10px}pre{white-space:pre-wrap;font-size:12px;background:#f8fafc;padding:10px;border-radius:8px}</style>
<div class='wrap'><h1>AGY 任务监控</h1><div class='sub' id='summary'>正在读取本机任务状态…</div><div id='data' class='grid'></div></div>
<script>function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function refresh(){try{let d=await (await fetch('/api/jobs')).json();let active=d.filter(x=>['queued','running'].includes(x.state)).length;document.querySelector('#summary').textContent='活动任务 '+active+' 个 · 共记录 '+d.length+' 个 · 每 5 秒自动刷新';document.querySelector('#data').innerHTML=d.slice(0,30).map(x=>{let st=x.state||'unknown';let label={queued:'排队中',running:'执行中',completed:'已完成',failed:'失败',cancelled:'已取消',lost:'已失联'}[st]||st;return `<div class='card ${st}'><span class='badge'>${label}</span><h3>${esc(x.display_name||'AGY 任务')}</h3><div class='job'>${esc(x.job_id)}</div><div class='meta'>目录：${esc(x.workdir||'未提供')}<br>心跳：${esc(x.heartbeat_at||'暂无')}<br>耗时：${esc(x.elapsed_seconds??0)} 秒</div>${x.error?`<p style='color:#b91c1c'>${esc(x.error)}</p>`:''}<details><summary>查看原始详情</summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details></div>`}).join('')||`<div class='empty'>暂无任务记录</div>`}catch(e){document.querySelector('#summary').textContent='无法读取本地任务状态：'+e} }refresh();setInterval(refresh,5000)</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/api/jobs":
            records = agy_jobs.recent(limit=100)
            for item in records:
                key = item.get("task_key") or ""
                if key.startswith("workdir:"):
                    key = Path(key[8:]).name
                item["display_name"] = key or (Path(item["workdir"]).name if item.get("workdir") else f"AGY 任务 {item.get('job_id','')[:8]}")
            payload = json.dumps(records, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(payload); return
        payload = HTML.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(payload)

    def log_message(self, *_):
        pass


def main():
    p = argparse.ArgumentParser(); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8765); a = p.parse_args()
    print(f"AGY dashboard: http://{a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
