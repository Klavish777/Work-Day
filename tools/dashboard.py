"""Веб-дашборд агента: читает state/dashboard.json и показывает состояние.

Локальный веб-сервер без зависимостей (стандартная библиотека). Ничего не
торгует, только показывает: счёт, риск-лимиты, позиции, сигналы, последние
записи журнала сделок.

    python tools/dashboard.py --port 8787

Потом открой http://127.0.0.1:8787 в браузере на той же машине (или через
RDP к VPS). По умолчанию слушает 127.0.0.1 — наружу не выставлен специально.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # переопределяется аргументом --root

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MT5 AI-Agent — дашборд</title>
<style>
 :root { color-scheme: dark; }
 * { box-sizing: border-box; }
 body { margin:0; font: 14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
        background:#0e1116; color:#e6edf3; }
 header { padding:16px 22px; border-bottom:1px solid #21262d; display:flex;
          align-items:center; gap:14px; flex-wrap:wrap; }
 h1 { font-size:17px; margin:0; font-weight:600; }
 .pill { padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600; }
 .ok { background:#12261b; color:#3fb950; border:1px solid #238636; }
 .warn { background:#2d2305; color:#d29922; border:1px solid #9e6a03; }
 .bad { background:#2d0f13; color:#f85149; border:1px solid #da3633; }
 .muted { color:#8b949e; font-size:12px; }
 main { padding:20px 22px; display:grid; gap:18px; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); }
 section { background:#161b22; border:1px solid #21262d; border-radius:10px; padding:16px 18px; }
 section h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:#8b949e;
              margin:0 0 12px; font-weight:600; }
 .big { font-size:26px; font-weight:700; }
 .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:8px 16px; }
 table { width:100%; border-collapse:collapse; font-size:13px; }
 th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #21262d; }
 th { color:#8b949e; font-weight:500; font-size:12px; }
 .pos { color:#3fb950; } .neg { color:#f85149; }
 pre { white-space:pre-wrap; word-break:break-word; margin:0; font-size:12px; color:#c9d1d9; }
 .bar { height:8px; background:#21262d; border-radius:4px; overflow:hidden; margin-top:6px; }
 .bar > div { height:100%; background:linear-gradient(90deg,#3fb950,#d29922,#f85149); }
 footer { padding:10px 22px 30px; color:#484f58; font-size:12px; }
</style></head>
<body>
<header>
  <h1>🤖 MT5 AI-Agent</h1>
  <span id="mode" class="pill">—</span>
  <span id="state" class="pill">—</span>
  <span id="kill" class="pill ok">kill switch: снят</span>
  <span class="muted" id="ts"></span>
</header>
<main>
  <section><h2>Счёт</h2>
    <div class="big" id="equity">—</div>
    <div class="muted" id="acct"></div>
    <div class="grid2" style="margin-top:12px">
      <div><div class="muted">Баланс</div><div id="balance">—</div></div>
      <div><div class="muted">Результат дня</div><div id="pnl">—</div></div>
      <div><div class="muted">Открытых позиций</div><div id="npos">—</div></div>
      <div><div class="muted">Просадка от пика</div><div id="dd">—</div></div>
    </div>
  </section>
  <section><h2>Риск</h2>
    <div class="grid2">
      <div><div class="muted">Риск на сделку</div><div id="rpt">—</div></div>
      <div><div class="muted">Лимит дня</div><div id="dll">—</div></div>
      <div><div class="muted">Реализовано сегодня</div><div id="dre">—</div></div>
      <div><div class="muted">Входов сегодня</div><div id="dtr">—</div></div>
    </div>
    <div class="bar"><div id="ddbar" style="width:0%"></div></div>
    <p class="muted" id="summary" style="margin:10px 0 0"></p>
  </section>
  <section><h2>Позиции</h2><div id="positions"><span class="muted">нет данных</span></div></section>
  <section><h2>Сигналы последнего цикла</h2><div id="signals"><span class="muted">нет данных</span></div></section>
  <section><h2>Блокировки</h2><div id="blocked"><span class="muted">нет</span></div></section>
  <section><h2>Закрытые сделки (последний цикл)</h2><div id="closed"><span class="muted">нет</span></div></section>
</main>
<footer>Данные обновляются каждые 5 секунд. Дашборд ничего не торгует — только читает state/dashboard.json.</footer>
<script>
const fmt = (v, d=2) => (v===null||v===undefined) ? "—" : Number(v).toLocaleString("ru-RU",{minimumFractionDigits:d,maximumFractionDigits:d});
const sgn = v => (v>0?"pos":v<0?"neg":"");
function pill(el, text, cls){ el.textContent = text; el.className = "pill " + cls; }
async function refresh(){
  try {
    const r = await fetch("api/state", {cache:"no-store"});
    const d = await r.json();
    if (d.error){ document.getElementById("state").className="pill bad"; document.getElementById("state").textContent = d.error; return; }
    const acct = d.account || {}, risk = d.risk || {};
    document.getElementById("demo-banner").style.display = d.virtual_market ? "block" : "none";
    pill(document.getElementById("mode"), "режим: " + (d.mode||"—"), "ok");
    if (risk.halted){ pill(document.getElementById("state"), "ОСТАНОВЛЕН", "bad"); }
    else { pill(document.getElementById("state"), "работает", "ok"); }
    pill(document.getElementById("kill"), "kill switch: " + (risk.kill_switch ? "АКТИВЕН" : "снят"),
         risk.kill_switch ? "bad" : "ok");
    document.getElementById("ts").textContent = "обновлено: " + (d.ts||"—");
    document.getElementById("equity").textContent = fmt(acct.equity);
    document.getElementById("acct").textContent = acct.desc || "—";
    document.getElementById("balance").textContent = fmt(acct.balance);
    const pnl = acct.pnl_today_pct;
    const pe = document.getElementById("pnl");
    pe.textContent = (pnl>0?"+":"") + fmt(pnl) + "%";
    pe.className = sgn(pnl);
    document.getElementById("npos").textContent = acct.open_positions ?? "—";
    const dd = risk.drawdown_pct ?? 0;
    const de = document.getElementById("dd");
    de.textContent = fmt(dd) + "%";
    de.className = dd > 5 ? "neg" : "";
    document.getElementById("rpt").textContent = fmt(risk.risk_per_trade_pct) + "%";
    document.getElementById("dll").textContent = "-" + fmt(risk.daily_loss_limit_pct) + "%";
    const dr = risk.daily_realized ?? 0;
    const dre = document.getElementById("dre");
    dre.textContent = (dr>0?"+":"") + fmt(dr);
    dre.className = sgn(dr);
    document.getElementById("dtr").textContent = risk.daily_trades ?? "—";
    document.getElementById("ddbar").style.width = Math.min(100, (dd/Math.max(1,risk.daily_loss_limit_pct*2))*100) + "%";
    document.getElementById("summary").textContent = risk.summary || "";
    renderTable("positions", d.positions, ["ticket","symbol","side","volume","price_open","sl","tp","profit"]);
    renderTable("signals", d.signals, ["symbol","side","probability","reason"], {side:true});
    renderTable("blocked", d.blocked, ["symbol","reason"]);
    renderTable("closed", d.closed, ["symbol","pnl","ticket"], {pnl:true});
  } catch(e) {
    pill(document.getElementById("state"), "нет данных", "warn");
  }
}
function renderTable(id, rows, cols, opts={}){
  const el = document.getElementById(id);
  if (!rows || !rows.length){ el.innerHTML = '<span class="muted">нет данных</span>'; return; }
  let html = "<table><tr>" + cols.map(c=>`<th>${c}</th>`).join("") + "</tr>";
  for (const row of rows){
    html += "<tr>" + cols.map(c=>{
      let v = row[c];
      if (typeof v === "number") v = fmt(v, c==="probability"?3:2);
      if (c==="profit"||c==="pnl") { const n = row[c]; return `<td class="${sgn(n)}">${n>0?"+":""}${v}</td>`; }
      return `<td>${v ?? "—"}</td>`;
    }).join("") + "</tr>";
  }
  el.innerHTML = html + "</table>";
}
refresh(); setInterval(refresh, 5000);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    root = ROOT

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/state"):
            path = self.root / "state" / "dashboard.json"
            if not path.exists():
                payload = {"error": "агент ещё не завершил ни одного цикла (нет state/dashboard.json)"}
            else:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["server_time"] = datetime.now(timezone.utc).isoformat()
                except json.JSONDecodeError as exc:
                    payload = {"error": f"не читается dashboard.json: {exc}"}
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/events"):
            path = self.root / "logs" / "events.jsonl"
            lines: list[str] = []
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines()[-200:]
            body = ("[" + ",".join(line for line in lines if line.strip()) + "]").encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, *args) -> None:  # тихий лог
        pass


def main(argv: list[str] | None = None) -> int:
    global ROOT

    parser = argparse.ArgumentParser(description="Локальный дашборд агента (только чтение)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--root", default=str(ROOT),
                        help="папка, где лежат state/dashboard.json и logs/events.jsonl "
                             "(по умолчанию — корень проекта)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="127.0.0.1 = только этот ПК (безопасно). "
                             "0.0.0.0 = доступ из сети (только внутри защищённого VPS)")
    args = parser.parse_args(argv)

    ROOT = Path(args.root).resolve()
    Handler.root = ROOT

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Дашборд: http://{args.host}:{args.port}  (Ctrl+C — остановить)")
    print(f"Читает: {ROOT / 'state' / 'dashboard.json'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
