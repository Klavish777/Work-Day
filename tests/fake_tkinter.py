"""Минимальная подделка tkinter для дымовых тестов панели управления.

Зачем: саму панель нельзя запустить в среде без графики, но её код всё равно нужно
проверять. Подделка позволяет «нарисовать» окно, вызвать обновление и убедиться, что
панель не падает, правильно дёргает контроллер и не содержит ошибок в именах.
"""

from __future__ import annotations

from types import ModuleType


class TclError(Exception):
    pass


class _Widget:
    """Универсальный «виджет»: любой вызов — успешный no-op."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.configured: list[dict] = []
        self.inserted: list[str] = []

    # методы, которые должны возвращать осмысленные значения
    def get_children(self):
        return ()

    def get(self, *args, **kwargs):
        return ""

    def set(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None

    def insert(self, *args, **kwargs):
        self.inserted.append(" ".join(str(a) for a in args))
        return None

    def configure(self, *args, **kwargs):
        self.configured.append(kwargs)
        return None

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop


class _Variable:
    def __init__(self, value=None, **kwargs) -> None:
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


def install(monkeypatch) -> ModuleType:
    """Подменить tkinter и его подмодули на время теста. Возвращает «tkinter»."""

    def _module(name: str) -> ModuleType:
        module = ModuleType(name)
        monkeypatch.setitem(__import__("sys").modules, name, module)
        return module

    tk = _module("tkinter")
    tk.Tk = _Widget
    tk.TclError = TclError
    tk.StringVar = _Variable
    tk.BooleanVar = _Variable
    tk.Text = _Widget
    tk.Label = _Widget
    tk.Frame = _Widget
    tk.END = "end"

    ttk = _module("tkinter.ttk")
    for name in ("Style", "Frame", "Label", "Button", "Notebook", "Treeview", "Entry", "Labelframe", "Separator"):
        setattr(ttk, name, _Widget)

    messagebox = _module("tkinter.messagebox")
    messagebox.showinfo = lambda *a, **k: "ok"
    messagebox.showwarning = lambda *a, **k: "ok"
    messagebox.showerror = lambda *a, **k: "error"
    messagebox.askyesno = lambda *a, **k: True
    messagebox.askokcancel = lambda *a, **k: True

    scrolledtext = _module("tkinter.scrolledtext")
    scrolledtext.ScrolledText = _Widget

    tk.ttk = ttk
    tk.messagebox = messagebox
    tk.scrolledtext = scrolledtext
    return tk
