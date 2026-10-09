/* Work-Day AI Trader — dashboard logic (polling based, mobile friendly). */
"use strict";

const $ = (id) => document.getElementById(id);

let LAST_STATUS = null;
let pendingRealServer = null;
let lastMt5DetailShown = null;
const TF_LIST = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const LLM_AGENTS = [["market_analyst", "Market Analyst"],
                    ["strategy_trader", "Strategy Trader"],
                    ["risk_analyst", "Risk Analyst"],
                    ["central", "Central LLM"]];

// --------------------------------------------------------------------- //
async function api(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* ignore */ }
  return { ok: resp.ok, status: resp.status, data };
}

function pillClass(value) {
  const v = String(value || "").toUpperCase();
  if (["BUY", "BULLISH", "APPROVED", "EXECUTE", "TRUE", "UP"].includes(v)) return "buy";
  if (["SELL", "BEARISH", "REJECTED", "REJECT", "FALSE", "DOWN", "HIGH"].includes(v)) return "sell";
  if (["HOLD", "WAIT", "MEDIUM", "FLAT", "NEUTRAL"].includes(v)) return "hold";
  if (["CLOSE", "LOW"].includes(v)) return v === "CLOSE" ? "close" : "gray";
  return "gray";
}
function setPill(el, text) {
  el.textContent = text || "—";
  el.className = "pill " + pillClass(text);
}
function fmtMoney(v, cur = "$") {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const sign = v < 0 ? "-" : (v > 0 ? "+" : "");
  return `${sign}${cur}${Math.abs(v).toFixed(2)}`;
}
function plClass(v) { return v > 0 ? "pl-plus" : (v < 0 ? "pl-minus" : "pl-zero"); }
function fmtTs(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

// --------------------------------------------------------------------- //
function renderUpdateBanner(s) {
  const u = s.update || {};
  const banner = $("update-banner");
  if (u.has_update && u.latest && !sessionStorage.getItem("update-hidden")) {
    $("update-text").textContent =
      `Доступно обновление: ${s.app_name || "Work-Day"} ${u.latest} ` +
      `(у вас prototip v${s.version})`;
    $("update-link").href = u.url || "https://github.com/Klavish777/Work-Day/releases";
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderStatus(s) {
  LAST_STATUS = s;
  renderUpdateBanner(s);

  // live MT5 connection result on the settings card
  const detLine = `MT5 ${s.mt5.status}${s.mt5.detail ? " — " + s.mt5.detail : ""}`;
  if (detLine !== lastMt5DetailShown) {
    lastMt5DetailShown = detLine;
    const el = $("mt5-detail");
    if (el) el.textContent = detLine;
  }
  // badges
  const mt5 = s.mt5 || {};
  const mt5Badge = $("badge-mt5");
  mt5Badge.textContent = "MT5: " + (mt5.status || "—");
  mt5Badge.className = "badge " + (mt5.status === "CONNECTED" ? "green" :
                        mt5.status === "UNAVAILABLE" ? "amber" :
                        mt5.status === "ERROR" ? "red" : "gray");

  const mode = (s.settings && s.settings.mode) || "DEMO";
  const modeBadge = $("badge-mode");
  modeBadge.textContent = mode + (mode === "REAL" ? " ⚠" : "");
  modeBadge.className = "badge " + (mode === "REAL" ? "red" : "green");

  const st = s.engine_state || "STOPPED";
  const stBadge = $("badge-engine");
  stBadge.textContent = st;
  stBadge.className = "badge " + (st === "RUNNING" ? "green" :
                        st === "PAUSED" ? "amber" : "gray");
  $("account-type").textContent = (s.account && s.account.is_demo) ? "DEMO ACCOUNT" :
                                  (s.account ? "REAL ACCOUNT" : "");

  // price
  const t = s.tick;
  if (t) {
    const digits = 5;
    $("price").textContent = ((t.bid + t.ask) / 2).toFixed(digits);
    $("bid").textContent = t.bid.toFixed(digits);
    $("ask").textContent = t.ask.toFixed(digits);
    $("spread").textContent = Number(t.spread_points).toFixed(1) + " pts";
  } else {
    ["price", "bid", "ask", "spread"].forEach(id => $(id).textContent = "—");
  }

  // account
  const a = s.account;
  $("balance").textContent = a ? `${a.balance.toFixed(2)} ${a.currency}` : "—";
  $("equity").textContent = a ? `${a.equity.toFixed(2)} ${a.currency}` : "—";

  // position
  const p = s.position;
  if (p) {
    $("position").textContent = `${p.side} ${p.lot} @ ${p.open_price}`;
    const pl = $("pl");
    pl.textContent = fmtMoney(p.profit);
    pl.className = plClass(p.profit);
  } else {
    $("position").textContent = "NONE";
    const pl = $("pl"); pl.textContent = "$0.00"; pl.className = "pl-zero";
  }

  // AI pipeline
  const c = s.last_cycle;
  if (c) {
    setPill($("ai-market"), c.market ? `${c.market.signal} · ${c.market.trend}` : "—");
    setPill($("ai-strategy"), c.strategy ? c.strategy.action : "—");
    setPill($("ai-risk"), c.risk ? (c.risk.approved ? "APPROVED" : "REJECTED") : "—");
    setPill($("ai-llm"), c.llm_decision ? c.llm_decision.action : "—");
    const conf = c.llm_decision ? c.llm_decision.confidence : 0;
    setPill($("confidence"), Math.round(conf * 100) + "%");
    $("reason").textContent = (c.llm_decision && c.llm_decision.reason) ||
                              c.skipped_reason || "—";
  } else {
    ["ai-market", "ai-strategy", "ai-risk", "ai-llm"].forEach(id => setPill($(id), "—"));
    setPill($("confidence"), "—");
    $("reason").textContent = "No cycles yet — press START and connect MT5.";
  }
  $("skip-note").textContent = (s.skipped_reason && (!c || !c.market)) ? s.skipped_reason : "";

  renderStats(s.stats);
}

function renderStats(stats) {
  const g = $("stats-grid");
  if (!stats) return;
  const items = [
    ["Trades today", stats.trades_today],
    ["Closed", stats.closed_trades],
    ["Win rate", stats.win_rate + "%"],
    ["Total P/L", fmtMoney(stats.total_profit)],
    ["Realized today", fmtMoney(stats.realized_today)],
    ["Profit factor", stats.profit_factor ?? "—"],
    ["Avg win", fmtMoney(stats.avg_win)],
    ["Avg loss", fmtMoney(stats.avg_loss)],
  ];
  g.innerHTML = items.map(([k, v]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
}

// --------------------------------------------------------------------- //
async function pollStatus() {
  try {
    const r = await api("/api/status");
    if (r.ok && r.data) renderStatus(r.data);
  } catch (e) { /* server restarting */ }
}

async function loadJournal() {
  const [trades, decisions] = await Promise.all([
    api("/api/journal/trades?limit=60"),
    api("/api/journal/decisions?limit=15"),
  ]);
  const tbody = document.querySelector("#trades-table tbody");
  tbody.innerHTML = (trades.data || []).map(t => `
    <tr>
      <td>${t.ticket ?? t.id}</td>
      <td>${t.side}</td>
      <td>${t.lot}</td>
      <td>${fmtTs(t.open_ts)}<br><span class="muted">${(t.open_price ?? "—")}</span></td>
      <td>${t.close_ts ? fmtTs(t.close_ts) + "<br><span class='muted'>" + (t.close_price ?? "—") + "</span>" : "<b>OPEN</b>"}</td>
      <td class="${plClass(t.profit)}">${t.profit !== null && t.profit !== undefined ? fmtMoney(t.profit) : "—"}</td>
      <td class="muted">${(t.reason_close || t.reason_open || "").slice(0, 80)}</td>
    </tr>`).join("");

  const box = $("decisions");
  box.innerHTML = (decisions.data || []).map(d => {
    const p = d.payload || {};
    const m = p.market || {}, st = p.strategy || {}, llm = p.llm_decision || {};
    return `<div class="cycle">
      <div class="head">
        <span class="pill ${pillClass(llm.action)}">${llm.action || "SKIP"}</span>
        <span class="pill gray">${d.engine_state}</span>
        <span class="pill gray">${d.mt5_status}</span>
        <span class="muted">${fmtTs(d.ts)}</span>
      </div>
      <div class="muted">Market: <b>${m.signal || "—"}</b> (${Math.round((m.confidence || 0) * 100)}%) ·
        Strategy: <b>${st.action || "—"}</b> ·
        Risk: <b>${p.risk ? (p.risk.approved ? "APPROVED" : "REJECTED") : "—"}</b></div>
      <div>${(llm.reason || p.skipped_reason || "").slice(0, 220)}</div>
    </div>`;
  }).join("") || '<div class="muted">No decisions recorded yet.</div>';
}

// --------------------------------------------------------------------- //
function fillSettings(s) {
  $("cfg-lot").value = s.lot_size;
  $("cfg-max-lot").value = s.max_lot;
  $("cfg-target").value = s.profit_target_usd;
  $("cfg-min-profit").value = s.min_profit_usd;
  $("cfg-max-profit").value = s.max_profit_usd;
  $("cfg-sl-usd").value = s.stop_loss_usd;
  $("cfg-tp-points").value = s.take_profit_points;
  $("cfg-sl-points").value = s.stop_loss_points;
  $("cfg-spread").value = s.max_spread_points;
  $("cfg-conf").value = s.min_confidence;
  $("cfg-rr").value = s.min_risk_reward;
  $("cfg-cycle").value = s.engine_cycle_sec;
  $("cfg-max-trades").value = s.max_trades_per_day;
  $("cfg-daily-loss").value = s.max_daily_loss_usd;
  $("cfg-auto").checked = !!s.auto_trading_enabled;
  $("cfg-require-sl").checked = !!s.require_sl;

  const tfBox = $("tf-box");
  tfBox.innerHTML = TF_LIST.map(tf =>
    `<label><input type="checkbox" value="${tf}" ${s.timeframes.includes(tf) ? "checked" : ""}> ${tf}</label>`).join("");

  const l = s.llm || {};
  $("cfg-llm-provider").value = l.provider || "off";
  $("cfg-llm-model").value = l.model || "";
  $("cfg-llm-url").value = l.base_url || "";
  $("cfg-llm-env").value = l.api_key_env || "";
  const agBox = $("llm-agents-box");
  agBox.innerHTML = LLM_AGENTS.map(([key, label]) =>
    `<label><input type="checkbox" id="llm-agent-${key}" ${(l.agents || {})[key] !== false ? "checked" : ""}> ${label}</label>`).join("");

  const m = s.mt5 || {};
  $("cfg-terminal").value = m.terminal_path || "";
  $("cfg-login").value = m.login || "";
  $("cfg-server").value = m.server || "";
  // reflect the saved server in the Bybit preset dropdown
  const sel = $("cfg-server-select");
  if (sel) {
    const known = [...sel.options].some(o => o.value === m.server);
    sel.value = known ? m.server : "__custom";
  }
}

async function saveSettings(showResult = true) {
  const tfs = [...document.querySelectorAll("#tf-box input:checked")].map(i => i.value);
  const patch = {
    lot_size: +$("cfg-lot").value,
    max_lot: +$("cfg-max-lot").value,
    profit_target_usd: +$("cfg-target").value,
    min_profit_usd: +$("cfg-min-profit").value,
    max_profit_usd: +$("cfg-max-profit").value,
    stop_loss_usd: +$("cfg-sl-usd").value,
    take_profit_points: +$("cfg-tp-points").value,
    stop_loss_points: +$("cfg-sl-points").value,
    max_spread_points: +$("cfg-spread").value,
    min_confidence: +$("cfg-conf").value,
    min_risk_reward: +$("cfg-rr").value,
    engine_cycle_sec: +$("cfg-cycle").value,
    max_trades_per_day: +$("cfg-max-trades").value,
    max_daily_loss_usd: +$("cfg-daily-loss").value,
    auto_trading_enabled: $("cfg-auto").checked,
    require_sl: $("cfg-require-sl").checked,
    timeframes: tfs,
  };
  const r = await api("/api/settings", "POST", { settings: patch });
  if (showResult) {
    $("settings-msg").textContent = r.ok ? "✓ Saved." :
      "✗ " + ((r.data && r.data.errors) || ["Failed"]).join("; ");
  }
  return r;
}

async function saveLLM() {
  const agents = {};
  for (const [key] of LLM_AGENTS) agents[key] = $(`llm-agent-${key}`).checked;
  const patch = { llm: {
    provider: $("cfg-llm-provider").value,
    model: $("cfg-llm-model").value.trim(),
    base_url: $("cfg-llm-url").value.trim(),
    api_key_env: $("cfg-llm-env").value.trim(),
    agents,
  }};
  const key = $("cfg-llm-key").value;
  if (key) patch.llm.api_key = key;
  const r = await api("/api/settings", "POST", { settings: patch });
  $("settings-msg").textContent = r.ok ? "✓ LLM settings saved." :
    "✗ " + ((r.data && r.data.errors) || ["Failed"]).join("; ");
}

// --------------------------------------------------------------------- //
function bindControls() {
  $("btn-start").onclick = () => api("/api/engine/start", "POST");
  $("btn-pause").onclick = () => api("/api/engine/pause", "POST");
  $("btn-stop").onclick = () => api("/api/engine/stop", "POST");

  // Bybit TradFi server preset: picks the server and syncs DEMO/REAL mode
  const sel = $("cfg-server-select");
  if (sel) {
    sel.onchange = () => {
      const v = sel.value;
      if (v === "__custom") return;
      $("cfg-server").value = v;
      const needReal = /^bybit-live/i.test(v);
      if (needReal && (!LAST_STATUS || LAST_STATUS.settings.mode !== "REAL")) {
        pendingRealServer = v;
        openRealModal();
      } else if (!needReal && LAST_STATUS && LAST_STATUS.settings.mode === "REAL") {
        setMode("DEMO");  // Bybit-Demo forces demo mode
      }
    };
  }
  $("btn-close-pos").onclick = async () => {
    if (confirm("Close the open position now?")) await api("/api/position/close", "POST");
  };
  $("btn-close-all").onclick = async () => {
    if (confirm("Close ALL positions on the account?")) await api("/api/position/close-all", "POST");
  };
  $("btn-mode").onclick = () => {
    const mode = (LAST_STATUS && LAST_STATUS.settings.mode) || "DEMO";
    if (mode === "DEMO") openRealModal(); else setMode("DEMO");
  };

  $("btn-mt5-connect").onclick = async () => {
    $("mt5-detail").textContent = "Connecting…";
    const r = await api("/api/mt5/connect", "POST", {
      terminal_path: $("cfg-terminal").value.trim(),
      login: $("cfg-login").value.trim(),
      password: $("cfg-password").value,
      server: $("cfg-server").value.trim(),
    });
    $("mt5-detail").textContent = r.ok ? "Connection request queued." :
      "Failed: " + JSON.stringify(r.data);
    $("cfg-password").value = "";
  };
  $("btn-mt5-disconnect").onclick = () => api("/api/mt5/disconnect", "POST");

  $("btn-save-settings").onclick = () => saveSettings();
  $("btn-save-llm").onclick = saveLLM;
  $("btn-set-demo").onclick = () => setMode("DEMO");
  $("btn-set-real").onclick = openRealModal;
  $("btn-real-cancel").onclick = () => $("modal-real").classList.add("hidden");
  $("btn-real-ok").onclick = confirmReal;
  $("update-hide").onclick = () => {
    sessionStorage.setItem("update-hidden", "1");
    $("update-banner").classList.add("hidden");
  };

  document.querySelectorAll(".bottom-nav button").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".bottom-nav button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "journal") loadJournal();
    };
  });
}

async function setMode(mode, confirmPhrase = "") {
  const r = await api("/api/settings", "POST", {
    settings: { mode }, real_confirm: confirmPhrase,
  });
  if (!r.ok) alert((r.data && r.data.errors || ["Failed"]).join("; "));
  $("modal-real").classList.add("hidden");
  // keep the server field in sync after a REAL confirmation
  if (mode === "REAL" && pendingRealServer) {
    $("cfg-server").value = pendingRealServer;
    pendingRealServer = null;
  }
}
function openRealModal() { $("modal-real").classList.remove("hidden"); }
async function confirmReal() {
  const phrase = $("real-confirm-input").value.trim();
  await setMode("REAL", phrase);
  $("real-confirm-input").value = "";
}

// --------------------------------------------------------------------- //
async function init() {
  bindControls();
  const r = await api("/api/status");
  if (r.ok && r.data) { renderStatus(r.data); fillSettings(r.data.settings); }
  pollStatus();
  setInterval(pollStatus, 2000);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}
init();
