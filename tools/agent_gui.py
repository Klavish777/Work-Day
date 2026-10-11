"""Панель управления агентом для Windows (встроенный tkinter, без зависимостей).

Что это: удобный «пульт» для ежедневной работы — статус, счёт, позиции, сигналы,
журнал сделок, кнопки запуска/остановки и аварийного стопа, запуск train/backtest.

Чем это НЕ является: панель не считает риск, не строит сигналы и не отправляет
ордера. Всю работу делает агент (тот же CLI, что и в консоли), а панель только
показывает состояние и запускает его отдельным процессом.

Что специально НЕ выведено в интерфейс:

* **реальный счёт.** Из панели доступны только `shadow` (наблюдение без ордеров)
  и `paper` (демо-счёт). Переход на реал требует трёх независимых подтверждений
  и запускается из консоли — см. docs/SAFETY.md;
* **правка риск-лимитов в один клик.** Лимиты меняются только в config.yaml и
  вступают в силу после перезапуска агента. Это осознанное трение: решение
  «увеличить риск» должно требовать усилия.

Запуск:

    .\.venv\Scripts\python.exe tools\agent_gui.py
    # или без окна консоли: .\.venv\Scripts\pythonw.exe tools\agent_gui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mt5_agent.control import AgentController, format_events  # noqa: E402
from mt5_agent.utils import fmt_money  # noqa: E402

REFRESH_MS = 3000

COLORS = {
    "ok": "#1f6f3d",
    "warn": "#8a6100",
    "bad": "#8c1f1f",
    "idle": "#3b4252",
    "bg": "#1b1f27",
    "panel": "#232936",
    "text": "#e6edf3",
    "muted": "#9aa4b2",
}


class AgentPanel(tk.Tk):
    """Главное окно панели."""

    def __init__(self, controller: AgentController) -> None:
        super().__init__()
        self.controller = controller
        self.agent_process: subprocess.Popen | None = None
        self.task_process: subprocess.Popen | None = None
        self.task_log_position = 0

        self.title("MT5 AI-Agent — панель управления")
        self.geometry("1040x720")
        self.minsize(900, 600)
        self.configure(bg=COLORS["bg"])
        self._setup_styles()
        self._build_header()
        self._build_tabs()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh()

    # ------------------------------------------------------------------ оформление
    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("Head.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 15, "bold"))
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Big.Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 20, "bold"))
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI", 10))
        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"],
                        foreground=COLORS["text"], rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["muted"])

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(14, 12, 14, 6))
        header.pack(fill="x")

        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="🤖 MT5 AI-Agent", style="Head.TLabel").pack(anchor="w")
        self.status_label = tk.Label(left, text="загрузка…", bg=COLORS["idle"], fg="white",
                                     font=("Segoe UI", 11, "bold"), padx=12, pady=4)
        self.status_label.pack(anchor="w", pady=(6, 2))
        self.detail_label = ttk.Label(left, text="", style="Muted.TLabel", wraplength=640, justify="left")
        self.detail_label.pack(anchor="w")

        buttons = ttk.Frame(header)
        buttons.pack(side="right", anchor="n")

        row1 = ttk.Frame(buttons)
        row1.pack(fill="x")
        ttk.Button(row1, text="▶ Демо-торговля", command=lambda: self.start_agent("paper")).pack(side="left", padx=3)
        ttk.Button(row1, text="👁 Наблюдение (без ордеров)", command=lambda: self.start_agent("shadow")).pack(side="left", padx=3)
        ttk.Button(row1, text="💻 Проверить один цикл", command=self.run_single_dry_cycle).pack(side="left", padx=3)

        row2 = ttk.Frame(buttons)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="⏹ Остановить мягко", command=self.soft_stop).pack(side="left", padx=3)
        ttk.Button(row2, text="⛔ Kill switch", command=self.kill_switch_on).pack(side="left", padx=3)
        ttk.Button(row2, text="✔ Снять стоп", command=self.kill_switch_off).pack(side="left", padx=3)
        ttk.Button(row2, text="🔄 Обновить", command=self.refresh).pack(side="left", padx=3)

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(6, 6))

        self._build_overview_tab()
        self._build_positions_tab()
        self._build_journal_tab()
        self._build_tasks_tab()
        self._build_settings_tab()

    def _build_overview_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Обзор")

        cards = ttk.Frame(tab)
        cards.pack(fill="x")

        self.card_values: dict[str, tk.Label] = {}
        definitions = [
            ("equity", "Equity"), ("balance", "Баланс"), ("pnl", "Результат дня"),
            ("positions", "Открытых позиций"), ("drawdown", "Просадка от пика"),
            ("realized", "Реализовано сегодня"), ("trades", "Входов сегодня"), ("risk", "Риск на сделку"),
        ]
        for index, (key, title) in enumerate(definitions):
            card = tk.Frame(cards, bg=COLORS["panel"], padx=12, pady=10)
            card.grid(row=index // 4, column=index % 4, sticky="nsew", padx=5, pady=5)
            cards.columnconfigure(index % 4, weight=1)
            tk.Label(card, text=title, bg=COLORS["panel"], fg=COLORS["muted"],
                     font=("Segoe UI", 9)).pack(anchor="w")
            value = tk.Label(card, text="—", bg=COLORS["panel"], fg=COLORS["text"],
                             font=("Segoe UI", 16, "bold"))
            value.pack(anchor="w")
            self.card_values[key] = value

        ttk.Label(tab, text="Риск-лимиты и итоги", style="Muted.TLabel").pack(anchor="w", pady=(14, 4))
        self.risk_text = tk.Text(tab, height=6, bg=COLORS["panel"], fg=COLORS["text"],
                                 insertbackground=COLORS["text"], relief="flat", wrap="word",
                                 font=("Consolas", 10))
        self.risk_text.pack(fill="x")
        self.risk_text.configure(state="disabled")

        ttk.Label(tab, text="Сигналы последнего цикла", style="Muted.TLabel").pack(anchor="w", pady=(12, 4))
        self.signals_text = tk.Text(tab, height=7, bg=COLORS["panel"], fg=COLORS["text"],
                                    insertbackground=COLORS["text"], relief="flat", wrap="none",
                                    font=("Consolas", 10))
        self.signals_text.pack(fill="both", expand=True)
        self.signals_text.configure(state="disabled")

    def _build_positions_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Позиции")

        columns = ("ticket", "symbol", "side", "volume", "price_open", "sl", "tp", "profit")
        self.positions_table = ttk.Treeview(tab, columns=columns, show="headings", height=12)
        headings = {
            "ticket": "Тикет", "symbol": "Символ", "side": "Сторона", "volume": "Объём",
            "price_open": "Цена входа", "sl": "Стоп-лосс", "tp": "Тейк-профит", "profit": "Плавающий P/L",
        }
        widths = {"ticket": 90, "symbol": 100, "side": 90, "volume": 80,
                  "price_open": 110, "sl": 110, "tp": 110, "profit": 120}
        for column in columns:
            self.positions_table.heading(column, text=headings[column])
            self.positions_table.column(column, width=widths[column], anchor="center")
        self.positions_table.pack(fill="both", expand=True)
        self.positions_table.tag_configure("win", foreground="#3fb950")
        self.positions_table.tag_configure("loss", foreground="#f85149")

        self.blocked_text = tk.Text(tab, height=6, bg=COLORS["panel"], fg=COLORS["text"],
                                    relief="flat", wrap="word", font=("Consolas", 9))
        self.blocked_text.pack(fill="x", pady=(10, 0))
        self.blocked_text.configure(state="disabled")

    def _build_journal_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Журнал")

        ttk.Label(tab, text="События (входы, закрытия, блокировки, стопы)",
                  style="Muted.TLabel").pack(anchor="w")
        self.events_text = scrolledtext.ScrolledText(
            tab, height=14, bg=COLORS["panel"], fg=COLORS["text"], relief="flat",
            font=("Consolas", 9), wrap="none",
        )
        self.events_text.pack(fill="both", expand=True, pady=(4, 10))

        ttk.Label(tab, text="Лог агента", style="Muted.TLabel").pack(anchor="w")
        self.log_text = scrolledtext.ScrolledText(
            tab, height=10, bg=COLORS["panel"], fg=COLORS["text"], relief="flat",
            font=("Consolas", 9), wrap="none",
        )
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    def _build_tasks_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Задачи")

        ttk.Label(tab, text="Проверка окружения, данные, обучение и бэктест. "
                            "Каждая задача выполняется тем же CLI, что и в консоли.",
                  style="Muted.TLabel").pack(anchor="w")

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(8, 6))
        ttk.Label(controls, text="Символ:").pack(side="left")
        self.task_symbol = tk.StringVar(value=self._default_symbol())
        ttk.Entry(controls, textvariable=self.task_symbol, width=12).pack(side="left", padx=6)
        ttk.Label(controls, text="Бары:").pack(side="left", padx=(12, 0))
        self.task_bars = tk.StringVar(value="20000")
        ttk.Entry(controls, textvariable=self.task_bars, width=8).pack(side="left", padx=6)
        ttk.Label(controls, text="Дней для бэктеста:").pack(side="left", padx=(12, 0))
        self.task_days = tk.StringVar(value="180")
        ttk.Entry(controls, textvariable=self.task_days, width=6).pack(side="left", padx=6)

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(4, 8))
        ttk.Button(row, text="check — окружение и связь", command=self.task_check).pack(side="left", padx=3)
        ttk.Button(row, text="fetch — скачать данные", command=self.task_fetch).pack(side="left", padx=3)
        ttk.Button(row, text="train — обучение", command=self.task_train).pack(side="left", padx=3)
        ttk.Button(row, text="backtest — бэктест", command=self.task_backtest).pack(side="left", padx=3)
        ttk.Button(row, text="Очистить вывод", command=self.clear_task_log).pack(side="left", padx=3)

        self.task_status = ttk.Label(tab, text="задача не запущена", style="Muted.TLabel")
        self.task_status.pack(anchor="w", pady=(0, 6))

        self.task_text = scrolledtext.ScrolledText(
            tab, bg="#101419", fg="#d6deeb", relief="flat", font=("Consolas", 9), wrap="word",
        )
        self.task_text.pack(fill="both", expand=True)

    def _build_settings_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Настройки")

        ttk.Label(tab, text="Текущие правила (только чтение). Лимиты риска меняются в config.yaml "
                            "и применяются после перезапуска агента — специально без кнопки «сохранить».",
                  style="Muted.TLabel", wraplength=900, justify="left").pack(anchor="w")

        self.settings_table = ttk.Treeview(tab, columns=("param", "value"), show="headings", height=18)
        self.settings_table.heading("param", text="Параметр")
        self.settings_table.heading("value", text="Значение")
        self.settings_table.column("param", width=260, anchor="w")
        self.settings_table.column("value", width=640, anchor="w")
        self.settings_table.pack(fill="both", expand=True, pady=(10, 8))

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="📂 Открыть config.yaml", command=self.open_config).pack(side="left", padx=3)
        ttk.Button(row, text="📂 Открыть папку проекта", command=self.open_project_folder).pack(side="left", padx=3)
        ttk.Button(row, text="📂 Открыть reports", command=self.open_reports).pack(side="left", padx=3)

    def _build_footer(self) -> None:
        footer = ttk.Frame(self, padding=(14, 0, 14, 10))
        footer.pack(fill="x")
        self.footer_label = ttk.Label(footer, text="", style="Muted.TLabel")
        self.footer_label.pack(side="left")

    # ------------------------------------------------------------------ действия
    def _default_symbol(self) -> str:
        symbols = self.controller.cfg.trading.enabled_symbols
        return symbols[0].name if symbols else "EURUSD"

    def _active_process(self) -> bool:
        return self.agent_process is not None and self.agent_process.poll() is None

    def start_agent(self, mode: str) -> None:
        snapshot = self.controller.snapshot()
        if snapshot.running or self._active_process():
            messagebox.showinfo("Агент уже работает",
                                "Сначала останови текущий процесс (кнопка «Остановить мягко»).")
            return
        if self.controller.kill_switch_path.exists():
            messagebox.showwarning(
                "Kill switch активен",
                "Пока файл state/KILL_SWITCH на месте, агент не начнёт торговлю.\n"
                "Сначала нажми «Снять стоп», если уверен, что причина устранена.",
            )
            return

        problems = [text for ok, text in self.controller.preflight() if not ok]
        if problems:
            if not messagebox.askyesno(
                "Проверка перед запуском",
                "Есть замечания:\n\n" + "\n".join(f"• {p}" for p in problems) +
                "\n\nВсё равно запустить?",
            ):
                return

        description = ("демо-торговля (paper): ордера уходят на ДЕМО-счёт"
                       if mode == "paper" else
                       "наблюдение (shadow): агент считает и пишет в журнал, но НЕ отправляет ордера")
        if not messagebox.askyesno("Подтверждение", f"Запустить {description}?"):
            return

        try:
            self.agent_process = self.controller.start(mode=mode)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Не удалось запустить", str(exc))
            return
        messagebox.showinfo("Агент запущен",
                            f"Процесс запущен (PID {self.agent_process.pid}).\n"
                            "Первые данные появятся в течение цикла (до 60 секунд).")
        self.refresh()

    def run_single_dry_cycle(self) -> None:
        if self._active_process():
            messagebox.showinfo("Агент работает", "Один цикл можно проверять, когда агент не запущен.")
            return
        try:
            self.agent_process = self.controller.start(mode="paper", dry_run=True, once=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Не удалось запустить", str(exc))
            return
        messagebox.showinfo("Пробный цикл",
                            "Запущен один цикл в режиме paper с --dry-run: сигналы и риск считаются, "
                            "ордера НЕ отправляются. Результат — в журнале и папке reports.")

    def soft_stop(self) -> None:
        if not messagebox.askyesno(
            "Мягкая остановка",
            "Агент доработает текущий цикл и завершится.\n"
            "Открытые позиции НЕ закрываются — они остаются под защитой стоп-лоссов у брокера.\n\n"
            "Остановить?",
        ):
            return
        self.controller.request_stop("остановка из панели")
        messagebox.showinfo("Запрос отправлен",
                            "Агент остановится в течение одного цикла (до 60 секунд).")
        self.refresh()

    def kill_switch_on(self) -> None:
        if not messagebox.askyesno(
            "Аварийный стоп",
            "Kill switch: агент немедленно закроет ВСЕ свои позиции и остановится.\n"
            "Это действие для нештатной ситуации.\n\nАктивировать?",
        ):
            return
        self.controller.activate_kill_switch("активирован из панели")
        messagebox.showwarning("Kill switch активирован",
                               "Агент (если запущен) закроет свои позиции на ближайшем цикле и встанет.")
        self.refresh()

    def kill_switch_off(self) -> None:
        if not messagebox.askyesno(
            "Снять стоп",
            "Снять kill switch и сбросить флаг остановки?\n"
            "Делай это только если понял причину срабатывания и устранил её.",
        ):
            return
        changed = self.controller.deactivate_kill_switch(reset_halt=True)
        self.controller.cancel_stop_request()
        messagebox.showinfo("Готово", "Стоп снят." if changed else "Стоп и так был не активен.")
        self.refresh()

    # ------------------------------------------------------------------ задачи
    def _run_task(self, args: list[str], title: str) -> None:
        if self.task_process is not None and self.task_process.poll() is None:
            messagebox.showinfo("Задача выполняется", "Дождись завершения текущей задачи.")
            return
        if not messagebox.askyesno("Запуск задачи", f"Запустить «{title}»?\n\nКоманда: mt5_agent {' '.join(args)}"):
            return
        self.task_log_position = self.controller.task_log_path.stat().st_size \
            if self.controller.task_log_path.exists() else 0
        self.task_text.delete("1.0", "end")
        try:
            self.task_process = self.controller.run_task(args)
        except OSError as exc:
            messagebox.showerror("Не удалось запустить", str(exc))
            return
        self.task_status.configure(text=f"выполняется: {title} (PID {self.task_process.pid})")

    def task_check(self) -> None:
        self._run_task(["check"], "проверка окружения и связи")

    def task_fetch(self) -> None:
        self._run_task(["fetch", "--bars", self.task_bars.get().strip() or "20000", "--save-specs"],
                       "скачивание данных с брокера")

    def task_train(self) -> None:
        self._run_task(["train", "--symbol", self.task_symbol.get().strip().upper()], "обучение модели")

    def task_backtest(self) -> None:
        self._run_task([
            "backtest", "--symbol", self.task_symbol.get().strip().upper(),
            "--days", self.task_days.get().strip() or "180",
        ], "бэктест")

    def clear_task_log(self) -> None:
        self.task_text.delete("1.0", "end")

    def _poll_task(self) -> None:
        path = self.controller.task_log_path
        if path.exists():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(self.task_log_position)
                    chunk = fh.read()
                    self.task_log_position = fh.tell()
            except OSError:
                chunk = ""
            if chunk:
                self.task_text.insert("end", chunk)
                self.task_text.see("end")
        if self.task_process is not None and self.task_process.poll() is not None:
            code = self.task_process.returncode
            state = "завершена успешно" if code == 0 else f"завершена с кодом {code}"
            if code == 1:
                state += " (метрики ниже порогов — смотри вердикт в выводе)"
            self.task_status.configure(text=f"задача {state}")
            self.task_process = None

    # ------------------------------------------------------------------ обслуживание
    def open_config(self) -> None:
        self._open_path(self.controller.cfg.root_dir / "config" / "config.yaml")

    def open_project_folder(self) -> None:
        self._open_path(self.controller.cfg.root_dir)

    def open_reports(self) -> None:
        self._open_path(self.controller.cfg.root_dir / "reports")

    def _open_path(self, path: Path) -> None:
        try:
            if hasattr(os, "startfile"):     # Windows
                os.startfile(str(path))      # type: ignore[attr-defined]
            else:                            # pragma: no cover - для разработки вне Windows
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Не удалось открыть", f"{path}\n{exc}")

    def refresh(self) -> None:
        """Обновить все вкладки. Никогда не бросает исключение — окно должно жить."""
        try:
            self._refresh_status()
        except Exception as exc:  # pragma: no cover
            self.detail_label.configure(text=f"ошибка обновления статуса: {exc}")
        try:
            self._refresh_overview()
        except Exception as exc:  # pragma: no cover
            self.risk_text.configure(state="normal")
            self.risk_text.delete("1.0", "end")
            self.risk_text.insert("1.0", f"ошибка обновления: {exc}")
            self.risk_text.configure(state="disabled")
        try:
            self._refresh_positions()
        except Exception:  # pragma: no cover
            pass
        try:
            self._refresh_journal()
        except Exception:  # pragma: no cover
            pass
        try:
            self._refresh_settings()
        except Exception:  # pragma: no cover
            pass
        try:
            self._poll_task()
        except Exception:  # pragma: no cover
            pass

        self.after(REFRESH_MS, self.refresh)

    def _refresh_status(self) -> None:
        snapshot = self.controller.snapshot()
        if snapshot.kill_switch:
            color, text = COLORS["bad"], "⛔ KILL SWITCH — торговля остановлена, позиции закрываются"
        elif snapshot.halted:
            color, text = COLORS["bad"], f"⛔ ОСТАНОВЛЕН: {snapshot.halt_reason or 'сработал лимит'}"
        elif snapshot.running:
            color, text = COLORS["ok"], "✅ РАБОТАЕТ"
        elif snapshot.stop_requested:
            color, text = COLORS["warn"], "⏹ ЗАПРОШЕНА ОСТАНОВКА — агент завершит цикл"
        else:
            color, text = COLORS["idle"], "⚪ Агент не запущен"
        self.status_label.configure(text=text, bg=color)

        details = []
        if snapshot.heartbeat_age_sec is not None:
            details.append(f"последний цикл {snapshot.heartbeat_age_sec:.0f} сек. назад")
        else:
            details.append("циклов ещё не было")
        details.append(f"режим: {snapshot.mode}")
        if snapshot.dry_run:
            details.append("расчёт без ордеров (dry-run)")
        if snapshot.virtual_market:
            details.append("ВИРТУАЛЬНЫЙ РЫНОК (синтетика)")
        if snapshot.account_desc:
            details.append(snapshot.account_desc)
        self.detail_label.configure(text=" | ".join(details))

    def _refresh_overview(self) -> None:
        snapshot = self.controller.snapshot()
        values = {
            "equity": fmt_money(snapshot.equity),
            "balance": fmt_money(snapshot.balance),
            "pnl": "—" if snapshot.pnl_today_pct is None else f"{snapshot.pnl_today_pct:+.2f}%",
            "positions": str(snapshot.open_positions),
            "drawdown": f"{snapshot.drawdown_pct:.2f}%",
            "realized": f"{snapshot.daily_realized:+.2f}",
            "trades": str(snapshot.daily_trades),
            "risk": f"{snapshot.risk_per_trade_pct:.2f}%",
        }
        colors = {"pnl": "#3fb950" if (snapshot.pnl_today_pct or 0) >= 0 else "#f85149",
                  "realized": "#3fb950" if snapshot.daily_realized >= 0 else "#f85149",
                  "drawdown": "#f85149" if snapshot.drawdown_pct > 5 else COLORS["text"]}
        for key, text in values.items():
            label = self.card_values[key]
            label.configure(text=text, fg=colors.get(key, COLORS["text"]))

        cfg = self.controller.cfg
        lines = [
            f"Риск на сделку: {cfg.risk.risk_per_trade_pct}% equity   |   "
            f"дневной лимит: -{cfg.risk.max_daily_loss_pct}%   |   "
            f"недельный: -{cfg.risk.max_weekly_loss_pct}%   |   просадка: -{cfg.risk.max_drawdown_pct}%",
            f"Макс. позиций: {cfg.risk.max_open_positions}   |   сделок в день: {cfg.risk.max_daily_trades}   |   "
            f"пауза после убытка: {cfg.risk.cooldown_after_loss_min} мин.   |   мартингейл/сетки: запрещены",
            "",
            f"Реализовано сегодня: {snapshot.daily_realized:+.2f}   |   входов сегодня: {snapshot.daily_trades}"
            f"   |   просадка от пика: {snapshot.drawdown_pct:.2f}%",
        ]
        if snapshot.errors:
            lines.append("Ошибки: " + "; ".join(snapshot.errors[:3]))
        self.risk_text.configure(state="normal")
        self.risk_text.delete("1.0", "end")
        self.risk_text.insert("1.0", "\n".join(lines))
        self.risk_text.configure(state="disabled")

        rows = []
        for signal in snapshot.signals:
            side = {"buy": "ЛОНГ", "sell": "ШОРТ", "none": "НЕТ"}.get(str(signal.get("side")), "?")
            rows.append(
                f"{signal.get('symbol', '?'):<8} {side:<5} "
                f"p={float(signal.get('probability') or 0):.3f}  "
                f"SL={float(signal.get('sl_distance') or 0):.5f} TP={float(signal.get('tp_distance') or 0):.5f}  "
                f"{signal.get('reason', '')}"
            )
        self.signals_text.configure(state="normal")
        self.signals_text.delete("1.0", "end")
        self.signals_text.insert("1.0", "\n".join(rows) if rows else "Сигналов пока нет (агент не завершил ни одного цикла).")
        self.signals_text.configure(state="disabled")

        self.footer_label.configure(
            text=f"Проект: {self.controller.cfg.root_dir}   |   файл состояния: {self.controller.state_path}"
        )

    def _refresh_positions(self) -> None:
        snapshot = self.controller.snapshot()
        self.positions_table.delete(*self.positions_table.get_children())
        for position in snapshot.positions:
            profit = float(position.get("profit") or 0)
            tag = "win" if profit > 0 else ("loss" if profit < 0 else "")
            self.positions_table.insert("", "end", values=(
                position.get("ticket"), position.get("symbol"),
                {"buy": "лонг", "sell": "шорт"}.get(str(position.get("side")), position.get("side")),
                position.get("volume"), position.get("price_open"),
                position.get("sl"), position.get("tp"), f"{profit:+.2f}",
            ), tags=(tag,))

        blocked = snapshot.blocked
        text = "Блокировки последнего цикла:\n" + (
            "\n".join(f"• {item.get('symbol')}: {item.get('reason')}" for item in blocked)
            if blocked else "нет — либо циклов ещё не было, либо входы проходили"
        )
        self.blocked_text.configure(state="normal")
        self.blocked_text.delete("1.0", "end")
        self.blocked_text.insert("1.0", text)
        self.blocked_text.configure(state="disabled")

    def _refresh_journal(self) -> None:
        events = self.controller.tail_events(80)
        rendered = format_events(events) if events else "Событий пока нет."
        self.events_text.configure(state="normal")
        self.events_text.delete("1.0", "end")
        self.events_text.insert("1.0", rendered)
        self.events_text.see("end")
        self.events_text.configure(state="disabled")

        log_text = self.controller.tail_agent_log(120)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", log_text or "Лог пуст.")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_settings(self) -> None:
        rows = self.controller.config_summary()
        current = self.settings_table.get_children()
        if len(current) == len(rows):
            return
        self.settings_table.delete(*current)
        for parameter, value in rows:
            self.settings_table.insert("", "end", values=(parameter, value))

    def _on_close(self) -> None:
        if self._active_process():
            if not messagebox.askyesno(
                "Закрыть панель?",
                "Агент запущен панелью. Если закрыть окно, процесс останется работать "
                "(это нормально — он не зависит от панели).\n\nЗакрыть панель?",
            ):
                return
        self.destroy()


def main() -> int:
    try:
        controller = AgentController()
    except Exception as exc:
        # Панель может не найти проект — сообщаем понятно, а не падаем трейсбеком.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Не удалось запустить панель", f"Ошибка загрузки конфигурации:\n{exc}")
        return 2

    panel = AgentPanel(controller)
    panel.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
