/* Work-Day AI Trader — dashboard logic (RU/EN, polling, one-click close). */
"use strict";

const $ = (id) => document.getElementById(id);

let LAST_STATUS = null;
let pendingRealServer = null;
let lastMt5DetailShown = null;

// --------------------------------------------------------------------- //
// I18N
// --------------------------------------------------------------------- //
const I18N = {
  ru: {
    dl_update: "Скачать обновление",
    price_label: "ТЕКУЩАЯ ЦЕНА", bid: "БИД", ask: "АСК", spread: "СПРЕД",
    account_label: "СЧЁТ", balance: "БАЛАНС", equity: "ЭКВИТИ",
    open_position: "ОТКРЫТАЯ ПОЗИЦИЯ", current_pl: "ТЕКУЩИЙ P/L",
    ai_pipeline: "AI КОНВЕЙЕР", ai_market: "РЫНОК (AI)", strategy: "СТРАТЕГИЯ",
    risk: "РИСК", llm_decision: "РЕШЕНИЕ · ВОЛОДЯ", confidence: "УВЕРЕННОСТЬ",
    llm_reason: "ПРИЧИНА РЕШЕНИЯ",
    waiting: "Ожидание первого цикла…",
    control: "УПРАВЛЕНИЕ",
    btn_start: "СТАРТ", btn_pause: "ПАУЗА", btn_stop: "СТОП",
    btn_close_pos: "ЗАКРЫТЬ ПОЗИЦИЮ", btn_close_all: "ЗАКРЫТЬ ВСЁ",
    stats_label: "СТАТИСТИКА (СЕГОДНЯ / ВСЕГО)",
    journal_label: "ТОРГОВЫЙ ЖУРНАЛ",
    th_side: "Напр.", th_lot: "Лот", th_open: "Открытие", th_close: "Закрытие",
    th_reason: "Причина",
    decisions_label: "ЖУРНАЛ РЕШЕНИЙ AI (ПОСЛЕДНИЕ ЦИКЛЫ)",
    no_decisions: "Решений пока нет.",
    mt5_connection: "ПОДКЛЮЧЕНИЕ MT5 — BYBIT TRADFI / METAQUOTES",
    server_label: "Сервер",
    terminal_path: "Путь к терминалу (необязательно)",
    mt5_login: "Логин MT5 (ID счёта)", mt5_password: "Пароль MT5 (запоминается)",
    pwd_saved: "•••••• сохранён — оставьте пустым, чтобы использовать",
    server_input: "Сервер",
    btn_connect: "ПОДКЛЮЧИТЬ MT5", btn_disconnect: "ОТКЛЮЧИТЬ",
    trading_params: "ТОРГОВЫЕ ПАРАМЕТРЫ (только вручную — AI не меняет)",
    lot_size: "Лот", max_lot: "Макс. лот", profit_target: "Цель прибыли, $",
    min_profit: "Мин. граница прибыли, $", max_profit: "Макс. граница прибыли, $",
    stop_loss: "Стоп-лосс, $ (0 = выкл)",
    tp_points: "Тейк-профит, пункты (0 = авто)",
    sl_points: "Стоп-лосс, пункты (0 = авто)",
    max_spread: "Макс. спред, пункты", min_conf: "Мин. уверенность AI",
    min_rr: "Мин. риск/прибыль", cycle: "Цикл, сек",
    max_trades: "Макс. сделок в день", max_daily_loss: "Макс. убыток в день, $",
    auto_enabled: "Автоторговля разрешена", require_sl: "Требовать стоп-лосс",
    tfs_label: "РАЗРЕШЁННЫЕ ТАЙМФРЕЙМЫ",
    save_settings: "СОХРАНИТЬ НАСТРОЙКИ",
    llm_card: "LLM-ПРОВАЙДЕР (сменный: OpenAI / локальная / совместимая)",
    provider: "Провайдер", model: "Модель",
    api_key_env: "Переменная окружения ключа",
    llm_agents: "LLM ДЛЯ АГЕНТОВ", save_llm: "СОХРАНИТЬ НАСТРОЙКИ LLM",
    mode_card: "РЕЖИМ: DEMO / REAL",
    mode_text: "DEMO — тестовый режим. Включение REAL требует явного подтверждения.",
    set_demo: "ВКЛЮЧИТЬ DEMO", set_real: "ВКЛЮЧИТЬ REAL…",
    modal_title: "⚠️ REAL TRADING USES REAL MONEY.",
    modal_text: "Вы включаете торговлю реальными деньгами. Введите фразу подтверждения в точности:",
    modal_cancel: "ОТМЕНА", modal_real_ok: "ВКЛЮЧИТЬ РЕАЛЬНУЮ ТОРГОВЛЮ",
    tab_dashboard: "Дашборд", tab_journal: "Журнал", tab_settings: "Настройки",
    stat_trades_today: "Сделок сегодня", stat_closed: "Закрыто",
    stat_win_rate: "Винрейт", stat_total: "Общий P/L",
    stat_today: "Реализовано сегодня", stat_pf: "Профит-фактор",
    stat_avg_win: "Средний профит", stat_avg_loss: "Средний убыток",
    why_blocked: "⛔ Риск-менеджер: ", why_gate: "⛔ Гейт: ",
    why_wait: "⏳ Ожидание сигнала: ",
    strategies_label: "AI СТРАТЕГИИ — БИБЛИОТЕКА И ПАМЯТЬ АГЕНТОВ",
    chart_label: "ГРАФИК AUD/CAD",
    chart_hint: "Колесо — масштаб · перетаскивание — прокрутка · двойной клик — к эфиру",
    chart_history: "ИСТОРИЯ",
    manual_hint: "· ручная торговля: BUY/SELL",
    cmd_sent: "Команда отправлена",
    cmd_err: "Ошибка",
    mem_note: "Память агентов: ", mem_closed: "закрытых сделок из истории",
    mem_wins: "прибыльных", mem_losses: "убыточных",
    mem_learn: "Агенты запоминают каждую сделку и автоматически пересчитывают веса стратегий.",
    strat_trades: "сделок",
    update_avail: "Доступно обновление: ", you_have: " (у вас prototip v",
    saved: "✓ Сохранено.",
    connecting: "Подключение…", queued: "Запрос на подключение поставлен в очередь.",
    agent_market: "Market Analyst", agent_strategy: "Strategy Trader",
    agent_risk: "Risk Analyst", agent_central: "Central LLM",
    howto: "<b>Как подключиться:</b><br>1. Установите терминал MetaTrader 5 на этот компьютер.<br>2. Получите данные счёта:<br>&nbsp;&nbsp;• <b>Bybit TradFi</b>: в приложении Bybit → TradFi → MT5-аккаунт → скопируйте <b>MT5 ID</b> и <b>пароль</b>.<br>&nbsp;&nbsp;• <b>MetaQuotes-Demo</b>: в терминале MT5 → Файл → Открыть счёт → найдите «MetaQuotes» → создайте демо-счёт → используйте выданные <b>логин и пароль</b> (виртуальные $, идеально для теста).<br>3. Выберите сервер ниже: <b>*-Demo</b> = демо, <b>Bybit-Live…Live7</b> = реальные деньги.<br>4. Логин и пароль <b>запоминаются на этом компьютере</b> — при следующем запуске вводить их не нужно."
  },
  en: {
    dl_update: "Download update",
    price_label: "CURRENT PRICE", bid: "BID", ask: "ASK", spread: "SPREAD",
    account_label: "ACCOUNT", balance: "BALANCE", equity: "EQUITY",
    open_position: "OPEN POSITION", current_pl: "CURRENT P/L",
    ai_pipeline: "AI PIPELINE", ai_market: "AI MARKET", strategy: "STRATEGY",
    risk: "RISK", llm_decision: "DECISION · VOLODYA", confidence: "CONFIDENCE",
    llm_reason: "DECISION REASON",
    waiting: "Waiting for the first cycle…",
    control: "CONTROL",
    btn_start: "START", btn_pause: "PAUSE", btn_stop: "STOP",
    btn_close_pos: "CLOSE POSITION", btn_close_all: "CLOSE ALL",
    stats_label: "STATISTICS (TODAY / ALL)",
    journal_label: "TRADE JOURNAL",
    th_side: "Side", th_lot: "Lot", th_open: "Open", th_close: "Close",
    th_reason: "Reason",
    decisions_label: "AI DECISION LOG (LAST CYCLES)",
    no_decisions: "No decisions recorded yet.",
    mt5_connection: "MT5 CONNECTION — BYBIT TRADFI / METAQUOTES",
    server_label: "Server",
    terminal_path: "Terminal path (optional)",
    mt5_login: "MT5 Login (account ID)", mt5_password: "MT5 Password (remembered)",
    pwd_saved: "•••••• saved — leave empty to reuse",
    server_input: "Server",
    btn_connect: "CONNECT MT5", btn_disconnect: "DISCONNECT",
    trading_params: "TRADING PARAMETERS (manual only — AI never changes these)",
    lot_size: "Lot size", max_lot: "Max lot", profit_target: "Profit target, $",
    min_profit: "Min profit bound, $", max_profit: "Max profit bound, $",
    stop_loss: "Stop loss, $ (0 = off)",
    tp_points: "TP override, points (0 = auto)",
    sl_points: "SL override, points (0 = auto)",
    max_spread: "Max spread, points", min_conf: "Min AI confidence",
    min_rr: "Min risk/reward", cycle: "Cycle, sec",
    max_trades: "Max trades / day", max_daily_loss: "Max daily loss, $",
    auto_enabled: "Auto-trading enabled", require_sl: "Require stop-loss",
    tfs_label: "ALLOWED TIMEFRAMES",
    save_settings: "SAVE SETTINGS",
    llm_card: "LLM PROVIDER (swappable: OpenAI / local / compatible)",
    provider: "Provider", model: "Model",
    api_key_env: "API key env var",
    llm_agents: "LLM PER AGENT", save_llm: "SAVE LLM SETTINGS",
    mode_card: "MODE: DEMO / REAL",
    mode_text: "DEMO is the testing mode. Switching to REAL requires explicit confirmation.",
    set_demo: "SET DEMO", set_real: "SET REAL…",
    modal_title: "⚠️ REAL TRADING USES REAL MONEY.",
    modal_text: "You are about to enable live trading. Type the confirmation phrase exactly:",
    modal_cancel: "CANCEL", modal_real_ok: "ENABLE REAL TRADING",
    tab_dashboard: "Dashboard", tab_journal: "Journal", tab_settings: "Settings",
    stat_trades_today: "Trades today", stat_closed: "Closed",
    stat_win_rate: "Win rate", stat_total: "Total P/L",
    stat_today: "Realized today", stat_pf: "Profit factor",
    stat_avg_win: "Avg win", stat_avg_loss: "Avg loss",
    why_blocked: "⛔ Risk manager: ", why_gate: "⛔ Gate: ",
    why_wait: "⏳ Waiting for a signal: ",
    strategies_label: "AI STRATEGIES — LIBRARY & AGENT MEMORY",
    chart_label: "AUD/CAD CHART",
    chart_hint: "Wheel — zoom · drag — scroll · double-click — back to live",
    chart_history: "HISTORY",
    manual_hint: "· manual trading: BUY/SELL",
    cmd_sent: "Command sent",
    cmd_err: "Error",
    mem_note: "Agent memory: ", mem_closed: "closed trades from history",
    mem_wins: "winners", mem_losses: "losers",
    mem_learn: "Agents remember every trade and re-weight the strategies automatically.",
    strat_trades: "trades",
    update_avail: "Update available: ", you_have: " (you have prototip v",
    saved: "✓ Saved.",
    connecting: "Connecting…", queued: "Connection request queued.",
    agent_market: "Market Analyst", agent_strategy: "Strategy Trader",
    agent_risk: "Risk Analyst", agent_central: "Central LLM",
    howto: "<b>How to connect:</b><br>1. Install the MetaTrader 5 terminal on this computer.<br>2. Get account credentials:<br>&nbsp;&nbsp;• <b>Bybit TradFi</b>: Bybit app → TradFi → MT5 account → copy <b>MT5 ID</b> and <b>password</b>.<br>&nbsp;&nbsp;• <b>MetaQuotes-Demo</b>: MT5 terminal → File → Open an Account → search “MetaQuotes” → create a demo account → use the issued <b>login and password</b>.<br>3. Pick a server below: <b>*-Demo</b> = demo, <b>Bybit-Live…Live7</b> = real money.<br>4. Login and password are <b>remembered on this computer</b> — you won't need to re-enter them next time."
  }
};

let LANG = localStorage.getItem("workday-lang") || "ru";
function t(key) { return (I18N[LANG] || I18N.ru)[key] || I18N.en[key] || key; }

function applyLang() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.innerHTML = t(el.getAttribute("data-i18n"));
  });
  $("howto-box").innerHTML = t("howto");
  $("lang-btn").textContent = LANG.toUpperCase();
  if ($("reason").dataset.default === "1") $("reason").textContent = t("waiting");
  if (LAST_STATUS) { renderStats(LAST_STATUS.stats); }
}

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
      t("update_avail") + `${s.app_name || "Work-Day"} ${u.latest}` +
      t("you_have") + s.version + ")";
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
  let detLine = `MT5 ${s.mt5.status}${s.mt5.detail ? " — " + s.mt5.detail : ""}`;
  if (s.last_connect) {
    const when = new Date(s.last_connect.ts * 1000).toLocaleTimeString();
    detLine = (s.last_connect.ok ? "✓ " : "✗ ") +
              (s.last_connect.detail || "no details") + `  [${when}]`;
  }
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
  $("account-type").textContent = (s.account && s.account.is_demo) ? "DEMO" :
                                  (s.account ? "REAL" : "");

  // price
  const t5 = s.tick;
  if (t5) {
    const digits = 5;
    $("price").textContent = ((t5.bid + t5.ask) / 2).toFixed(digits);
    $("bid").textContent = t5.bid.toFixed(digits);
    $("ask").textContent = t5.ask.toFixed(digits);
    $("spread").textContent = Number(t5.spread_points).toFixed(1) + " pts";
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
    $("position").textContent = LANG === "ru" ? "НЕТ" : "NONE";
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
    $("reason").dataset.default = "0";
  } else {
    ["ai-market", "ai-strategy", "ai-risk", "ai-llm"].forEach(id => setPill($(id), "—"));
    setPill($("confidence"), "—");
    $("reason").textContent = t("waiting");
    $("reason").dataset.default = "1";
  }
  $("skip-note").textContent = (s.skipped_reason && (!c || !c.market)) ? s.skipped_reason : "";

  renderStrategies(s);

  // why is the bot not trading right now?
  let why = "";
  if (c && !s.position) {
    if (c.executed && c.executed.action === "BLOCKED_BY_RISK_MANAGER") {
      why = t("why_blocked") + (c.executed.reasons || []).join("; ");
    } else if (c.gate && !c.gate.approved) {
      why = t("why_gate") + (c.gate.reasons || []).join("; ");
    } else if (c.llm_decision && c.llm_decision.action === "HOLD") {
      why = t("why_wait") +
            ((c.llm_decision.reason || (c.consensus || {}).reason) || "");
    }
  }
  $("why-not").textContent = why;

  renderStats(s.stats);
}

let LAST_CATALOG = {};

function renderStrategies(s) {
  const box = $("strat-votes");
  const votes = (s.last_cycle && s.last_cycle.market &&
                 s.last_cycle.market.strategies) || null;
  const mem = s.agent_memory || {};
  const stats = mem.strategies || {};
  if (s.strategy_catalog) LAST_CATALOG = s.strategy_catalog;
  if (!votes) {
    box.innerHTML = "";
  } else {
    box.innerHTML = Object.keys(votes).map(name => {
      const v = votes[name] || {};
      const cat = LAST_CATALOG[name] || [name, ""];
      const st = stats[name];
      const cls = v.vote === "BUY" ? "buy" : v.vote === "SELL" ? "sell" : "gray";
      const arrow = v.vote === "BUY" ? "▲" : v.vote === "SELL" ? "▼" : "•";
      const w = st ? `×${st.weight}` : "";
      const perf = (st && st.trades) ? `${st.trades} ${t("strat_trades")}` : "";
      return `<div class="strat-chip" title="${cat[1]}">
        <span class="strat-name">${cat[0]}</span>
        <span class="pill ${cls}">${arrow} ${v.vote}</span>
        ${w ? `<span class="strat-w">${w}</span>` : ""}
        ${perf ? `<span class="strat-perf">${perf}</span>` : ""}
      </div>`;
    }).join("");
  }
  const b = mem.baseline;
  $("memory-note").textContent = b ?
    `${t("mem_note")}${b.closed} ${t("mem_closed")} ` +
    `(${b.wins} ${t("mem_wins")}, ${b.losses} ${t("mem_losses")}, ` +
    `P/L ${fmtMoney(b.pnl)}). ${t("mem_learn")}` : "";
}

function renderStats(stats) {
  const g = $("stats-grid");
  if (!stats) return;
  const items = [
    [t("stat_trades_today"), stats.trades_today],
    [t("stat_closed"), stats.closed_trades],
    [t("stat_win_rate"), stats.win_rate + "%"],
    [t("stat_total"), fmtMoney(stats.total_profit)],
    [t("stat_today"), fmtMoney(stats.realized_today)],
    [t("stat_pf"), stats.profit_factor ?? "—"],
    [t("stat_avg_win"), fmtMoney(stats.avg_win)],
    [t("stat_avg_loss"), fmtMoney(stats.avg_loss)],
  ];
  g.innerHTML = items.map(([k, v]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
}

// --------------------------------------------------------------------- //
// Fast path: price / balance / position updated every 100 ms
// --------------------------------------------------------------------- //
async function pollQuick() {
  try {
    const r = await api("/api/quick");
    if (r.ok && r.data) renderQuick(r.data);
  } catch (e) { /* server restarting */ }
}

function renderQuick(s) {
  LAST_QUICK = s;
  const mt5Badge = $("badge-mt5");
  const mstat = s.mt5_status || "—";
  mt5Badge.textContent = "MT5: " + mstat;
  mt5Badge.className = "badge " + (mstat === "CONNECTED" ? "green" :
                        mstat === "UNAVAILABLE" ? "amber" :
                        mstat === "ERROR" ? "red" : "gray");

  const st = s.engine_state || "STOPPED";
  const stBadge = $("badge-engine");
  stBadge.textContent = st;
  stBadge.className = "badge " + (st === "RUNNING" ? "green" :
                        st === "PAUSED" ? "amber" : "gray");

  // buttons reflect the real engine state
  $("btn-start").disabled = (st === "RUNNING");
  $("btn-pause").disabled = (st !== "RUNNING");
  $("btn-stop").disabled = (st === "STOPPED");

  // manual BUY/SELL available only when no position is open
  const hasPos = !!(s.position && s.position.ticket);
  $("btn-buy").disabled = hasPos;
  $("btn-sell").disabled = hasPos;
  $("btn-buy-chart").disabled = hasPos;
  $("btn-sell-chart").disabled = hasPos;

  const mode = s.mode || "DEMO";
  const modeBadge = $("badge-mode");
  modeBadge.textContent = mode + (mode === "REAL" ? " ⚠" : "");
  modeBadge.className = "badge " + (mode === "REAL" ? "red" : "green");

  const t5 = s.tick;
  if (t5) {
    const digits = 5;
    $("price").textContent = ((t5.bid + t5.ask) / 2).toFixed(digits);
    $("bid").textContent = t5.bid.toFixed(digits);
    $("ask").textContent = t5.ask.toFixed(digits);
    $("spread").textContent = Number(t5.spread_points).toFixed(1) + " pts";
  }

  const a = s.account;
  if (a) {
    $("balance").textContent = `${Number(a.balance).toFixed(2)} ${a.currency}`;
    $("equity").textContent = `${Number(a.equity).toFixed(2)} ${a.currency}`;
  }

  const p = s.position;
  if (p) {
    $("position").textContent = `${p.side} ${p.lot} @ ${p.open_price}`;
    const pl = $("pl");
    pl.textContent = fmtMoney(p.profit);
    pl.className = plClass(p.profit);
  } else if (LAST_STATUS && !LAST_STATUS.position) {
    $("position").textContent = LANG === "ru" ? "НЕТ" : "NONE";
    const pl = $("pl"); pl.textContent = "$0.00"; pl.className = "pl-zero";
  }
}

// --------------------------------------------------------------------- //
// Candlestick chart with timeframe switcher (1s..4h), zoom, pan, LIVE
// --------------------------------------------------------------------- //
let CHART_TF = "S5";
let CHART_CANDLES = [];      // [{t,o,h,l,c}, ...]
let CHART_VIEW = { zoom: 1, offset: 0 };   // offset = bars back from live
let CHART_DRAG = null;
let CHART_TIMER = null;
let LAST_QUICK = null;

function chartLiveState() {
  const btn = $("chart-live");
  if (!btn) return;
  const live = CHART_VIEW.offset === 0;
  btn.classList.toggle("live", live);
  btn.classList.toggle("paused", !live);
  $("chart-live-text").textContent = live ? "LIVE" : t("chart_history");
}

function chartIntervalMs() {
  if (CHART_TF.startsWith("S")) return 1000;
  if (CHART_TF === "M1") return 2000;
  return 5000;
}

async function fetchCandles() {
  try {
    const r = await api(`/api/chart/candles?tf=${CHART_TF}&count=500`);
    if (r.ok && r.data) { CHART_CANDLES = r.data.candles || []; drawChart(); }
  } catch (e) { /* ignore */ }
}

function restartChartTimer() {
  if (CHART_TIMER) clearInterval(CHART_TIMER);
  CHART_TIMER = setInterval(fetchCandles, chartIntervalMs());
}

function selectTf(tf) {
  CHART_TF = tf;
  CHART_VIEW = { zoom: 1, offset: 0 };
  document.querySelectorAll("#chart-tfs button").forEach(b =>
    b.classList.toggle("active", b.dataset.tf === tf));
  fetchCandles();
  restartChartTimer();
}

function drawChart() {
  const canvas = $("chart");
  if (!canvas) return;
  chartLiveState();
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (w < 10 || h < 10) return;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr; canvas.height = h * dpr;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const total = CHART_CANDLES.length;
  if (total < 2) {
    ctx.fillStyle = "#98989d";
    ctx.font = "13px -apple-system, 'Segoe UI', sans-serif";
    ctx.fillText(LANG === "ru"
      ? "Ожидание данных… (подключите MT5; секундные таймфреймы наполняются с момента запуска)"
      : "Waiting for data… (connect MT5; second timeframes fill since app start)",
      14, h / 2);
    return;
  }

  const visible = Math.max(15, Math.floor(total / CHART_VIEW.zoom));
  const end = total - CHART_VIEW.offset;
  const start = Math.max(0, end - visible);
  const C = CHART_CANDLES.slice(start, end);
  const n = C.length;
  if (n < 2) return;

  const padL = 8, padR = 62, padT = 10, padB = 24;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  let min = Math.min(...C.map(c => c.l)), max = Math.max(...C.map(c => c.h));
  if (max - min < 1e-5) { min -= 0.0002; max += 0.0002; }
  const span = max - min;
  min -= span * 0.08; max += span * 0.08;

  const X = i => padL + ((i + 0.5) / n) * plotW;   // candle center
  const Y = v => padT + (1 - (v - min) / (max - min)) * plotH;

  ctx.font = "11px -apple-system, 'Segoe UI', sans-serif";

  // horizontal grid + price labels
  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.fillStyle = "#98989d";
  ctx.textAlign = "left";
  for (let g = 0; g <= 4; g++) {
    const v = min + ((max - min) * g) / 4;
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.fillText(v.toFixed(5), padL + plotW + 8, y + 3.5);
  }

  // vertical grid + time labels (timings)
  ctx.textAlign = "center";
  const useSec = CHART_TF.startsWith("S");
  const ticks = Math.max(2, Math.min(6, Math.floor(plotW / 130)));
  for (let g = 0; g <= ticks; g++) {
    const i = Math.round((g / ticks) * (n - 1));
    const x = X(i);
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke();
    const when = new Date(C[i].t * 1000);
    const lbl = useSec
      ? when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    ctx.fillStyle = "#98989d";
    ctx.fillText(lbl, Math.max(padL + 26, Math.min(x, padL + plotW - 26)), h - 8);
  }

  // candles
  const cw = Math.max(1.5, Math.min(15, (plotW / n) * 0.62));
  for (let i = 0; i < n; i++) {
    const c = C[i];
    const up = c.c >= c.o;
    const col = up ? "#30d158" : "#ff453a";
    const x = X(i);
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, Y(c.h)); ctx.lineTo(x, Y(c.l)); ctx.stroke();
    const yO = Y(c.o), yC = Y(c.c);
    const top = Math.min(yO, yC), bh = Math.max(1, Math.abs(yC - yO));
    ctx.fillStyle = col;
    ctx.fillRect(x - cw / 2, top, cw, bh);
  }

  // entry price line (open position)
  const pos = LAST_QUICK && LAST_QUICK.position;
  if (pos && pos.open_price >= min && pos.open_price <= max) {
    const y = Y(pos.open_price);
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = pos.side === "BUY" ? "#30d158" : "#ff453a";
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.textAlign = "left";
    ctx.fillStyle = pos.side === "BUY" ? "#30d158" : "#ff453a";
    ctx.fillText((LANG === "ru" ? "вход " : "entry ") + Number(pos.open_price).toFixed(5),
                 padL + 4, y - 5);
  }

  // live cursor on the last candle
  if (CHART_VIEW.offset === 0) {
    const last = C[n - 1];
    const lx = X(n - 1), ly = Y(last.c);
    ctx.beginPath(); ctx.arc(lx, ly, 3.2, 0, Math.PI * 2);
    ctx.fillStyle = "#0a84ff"; ctx.fill();
    ctx.beginPath(); ctx.arc(lx, ly, 6.5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(10,132,255,0.25)"; ctx.fill();
  }

  // time range caption
  const fmt = ts => new Date(ts * 1000).toLocaleTimeString([], useSec
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" });
  const range = $("chart-range");
  if (range) range.textContent = fmt(C[0].t) + " — " + fmt(C[n - 1].t);
}

function chartClampView() {
  const total = CHART_CANDLES.length;
  CHART_VIEW.zoom = Math.max(1, Math.min(32, CHART_VIEW.zoom));
  CHART_VIEW.offset = Math.max(0, Math.min(Math.max(0, total - 2),
                                           CHART_VIEW.offset));
}

function setupChart() {
  const canvas = $("chart");
  if (!canvas) return;
  if ("ResizeObserver" in window) {
    new ResizeObserver(() => drawChart()).observe(canvas);
  }
  window.addEventListener("resize", () => drawChart());

  // drag = scroll through history
  canvas.addEventListener("mousedown", (e) => {
    CHART_DRAG = { x: e.clientX, offset: CHART_VIEW.offset };
    canvas.style.cursor = "grabbing";
  });
  window.addEventListener("mousemove", (e) => {
    if (!CHART_DRAG || !CHART_CANDLES.length) return;
    const total = CHART_CANDLES.length;
    const visible = Math.max(15, Math.floor(total / CHART_VIEW.zoom));
    const barsPerPx = visible / Math.max(1, canvas.clientWidth);
    CHART_VIEW.offset = CHART_DRAG.offset +
      Math.round((e.clientX - CHART_DRAG.x) * barsPerPx);
    chartClampView();
    drawChart();
  });
  window.addEventListener("mouseup", () => {
    CHART_DRAG = null;
    if (canvas) canvas.style.cursor = "grab";
  });

  // wheel = zoom
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    CHART_VIEW.zoom *= (e.deltaY < 0 ? 1.25 : 0.8);
    chartClampView();
    drawChart();
  }, { passive: false });

  // double-click or LIVE button = back to live
  const goLive = () => { CHART_VIEW = { zoom: 1, offset: 0 }; drawChart(); };
  canvas.addEventListener("dblclick", goLive);
  const liveBtn = $("chart-live");
  if (liveBtn) liveBtn.onclick = goLive;

  // timeframe switcher
  document.querySelectorAll("#chart-tfs button").forEach(b => {
    b.onclick = () => selectTf(b.dataset.tf);
  });

  canvas.style.cursor = "grab";
  fetchCandles();
  restartChartTimer();
}

// --------------------------------------------------------------------- //
function showToast(msg, isError = false) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(el._h);
  el._h = setTimeout(() => el.classList.add("hidden"), 2400);
}

async function cmd(path, label) {
  const r = await api(path, "POST");
  if (r.ok) showToast("✓ " + label + ": " + t("cmd_sent"));
  else showToast("✗ " + label + ": " + t("cmd_err") + " " +
                 JSON.stringify(r.data || ""), true);
  return r;
}

async function manualTrade(side) {
  const r = await api("/api/trade/manual", "POST", { side });
  const d = r.data || {};
  if (r.ok && d.ok) {
    showToast(`✓ ${side} #${d.ticket} @ ${Number(d.price).toFixed(5)}`);
  } else {
    const msg = d.error || d.detail || JSON.stringify(d);
    showToast(`✗ ${side}: ${msg}`, true);
  }
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
  tbody.innerHTML = (trades.data || []).map(tt => `
    <tr>
      <td>${tt.ticket ?? tt.id}</td>
      <td>${tt.side}</td>
      <td>${tt.lot}</td>
      <td>${fmtTs(tt.open_ts)}<br><span class="muted">${(tt.open_price ?? "—")}</span></td>
      <td>${tt.close_ts ? fmtTs(tt.close_ts) + "<br><span class='muted'>" + (tt.close_price ?? "—") + "</span>" : "<b>OPEN</b>"}</td>
      <td class="${plClass(tt.profit)}">${tt.profit !== null && tt.profit !== undefined ? fmtMoney(tt.profit) : "—"}</td>
      <td class="muted">${(tt.reason_close || tt.reason_open || "").slice(0, 80)}</td>
    </tr>`).join("");

  const box = $("decisions");
  box.innerHTML = (decisions.data || []).map(d => {
    const p = d.payload || {};
    const m = p.market || {}, stt = p.strategy || {}, llm = p.llm_decision || {};
    return `<div class="cycle">
      <div class="head">
        <span class="pill ${pillClass(llm.action)}">${llm.action || "SKIP"}</span>
        <span class="pill gray">${d.engine_state}</span>
        <span class="pill gray">${d.mt5_status}</span>
        <span class="muted">${fmtTs(d.ts)}</span>
      </div>
      <div class="muted">Market: <b>${m.signal || "—"}</b> (${Math.round((m.confidence || 0) * 100)}%) ·
        Strategy: <b>${stt.action || "—"}</b> ·
        Risk: <b>${p.risk ? (p.risk.approved ? "APPROVED" : "REJECTED") : "—"}</b></div>
      <div>${(llm.reason || p.skipped_reason || "").slice(0, 220)}</div>
    </div>`;
  }).join("") || `<div class="muted">${t("no_decisions")}</div>`;
}

// --------------------------------------------------------------------- //
const TF_LIST = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const LLM_AGENTS = [["market_analyst", "agent_market"],
                    ["strategy_trader", "agent_strategy"],
                    ["risk_analyst", "agent_risk"],
                    ["central", "agent_central"]];

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
  agBox.innerHTML = LLM_AGENTS.map(([key, labelKey]) =>
    `<label><input type="checkbox" id="llm-agent-${key}" ${(l.agents || {})[key] !== false ? "checked" : ""}> ${t(labelKey)}</label>`).join("");

  const m = s.mt5 || {};
  $("cfg-terminal").value = m.terminal_path || "";
  $("cfg-login").value = m.login || "";
  $("cfg-server").value = m.server || "";
  const pwd = $("cfg-password");
  pwd.value = "";
  pwd.placeholder = m.password_saved ? t("pwd_saved") : "";
  const sel = $("cfg-server-select");
  if (sel) {
    const known = [...sel.options].some(o => o.value === m.server);
    sel.value = known ? m.server : "__custom";
  }
}

async function saveSettings() {
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
  $("settings-msg").textContent = r.ok ? t("saved") :
    "✗ " + ((r.data && r.data.errors) || ["Failed"]).join("; ");
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
  $("settings-msg").textContent = r.ok ? t("saved") :
    "✗ " + ((r.data && r.data.errors) || ["Failed"]).join("; ");
}

// --------------------------------------------------------------------- //
function bindControls() {
  $("btn-start").onclick = () => cmd("/api/engine/start", t("btn_start"));
  $("btn-pause").onclick = () => cmd("/api/engine/pause", t("btn_pause"));
  $("btn-stop").onclick = () => cmd("/api/engine/stop", t("btn_stop"));

  // ONE-CLICK close: no confirmation dialogs
  $("btn-close-pos").onclick = () => cmd("/api/position/close", t("btn_close_pos"));
  $("btn-close-all").onclick = () => cmd("/api/position/close-all", t("btn_close_all"));

  // MANUAL trading: BUY / SELL buttons of the trading bot
  $("btn-buy").onclick = () => manualTrade("BUY");
  $("btn-sell").onclick = () => manualTrade("SELL");
  // same buttons overlaid on the chart (BUY right, SELL left)
  $("btn-buy-chart").onclick = () => manualTrade("BUY");
  $("btn-sell-chart").onclick = () => manualTrade("SELL");

  $("btn-mode").onclick = () => {
    const mode = (LAST_STATUS && LAST_STATUS.settings.mode) || "DEMO";
    if (mode === "DEMO") openRealModal(); else setMode("DEMO");
  };

  $("lang-btn").onclick = () => {
    LANG = LANG === "ru" ? "en" : "ru";
    localStorage.setItem("workday-lang", LANG);
    applyLang();
    if (LAST_STATUS) renderStatus(LAST_STATUS);
  };

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
        setMode("DEMO");
      }
    };
  }

  $("btn-mt5-connect").onclick = async () => {
    $("mt5-detail").textContent = t("connecting");
    const pwd = $("cfg-password").value;
    const r = await api("/api/mt5/connect", "POST", {
      terminal_path: $("cfg-terminal").value.trim(),
      login: $("cfg-login").value.trim(),
      password: pwd,
      server: $("cfg-server").value.trim(),
    });
    $("mt5-detail").textContent = r.ok ? t("queued") :
      "Failed: " + JSON.stringify(r.data);
    $("cfg-password").value = "";
    if (r.ok && pwd) $("cfg-password").placeholder = t("pwd_saved");
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

  document.querySelectorAll(".top-nav button").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".top-nav button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach(tb => tb.classList.remove("active"));
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
  applyLang();
  const r = await api("/api/status");
  if (r.ok && r.data) { renderStatus(r.data); fillSettings(r.data.settings); }
  pollStatus();
  setInterval(pollStatus, 2000);
  pollQuick();
  setInterval(pollQuick, 100);   // fastest practical UI refresh
  setupChart();
}
init();
