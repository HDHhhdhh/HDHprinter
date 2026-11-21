"""Korean catalog lookup for the readonly material selectors."""
from __future__ import annotations

import re
import tkinter as tk
import unicodedata
from collections.abc import Callable, Sequence
from tkinter import ttk


_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def catalog_matches(values: Sequence[str], query: str) -> list[str]:
    """Match literal text or initial consonants, preserving the catalog's order."""
    terms = unicodedata.normalize("NFC", query).casefold().split()
    patterns = []
    for term in terms:
        parts = []
        for char in term:
            if "ᄀ" <= char <= "ᄒ":
                char = _INITIALS[ord(char) - ord("ᄀ")]
            if char in _INITIALS:
                first = ord("가") + _INITIALS.index(char) * 588
                parts.append(f"[{char}{chr(first)}-{chr(first + 587)}]")
            else:
                parts.append(re.escape(char))
        patterns.append(re.compile("".join(parts)))
    matches = []
    for value in values:
        compact = "".join(unicodedata.normalize("NFC", value).casefold().split())
        if all(pattern.search(compact) for pattern in patterns):
            matches.append(value)
    return matches


class CatalogSearch:
    """All catalog fields share a non-focus-stealing result list."""

    def __init__(self, root: tk.Tk, *, read_preedit: Callable, commit_preedit: Callable):
        self.root = root
        self.read_preedit = read_preedit
        self.commit_preedit = commit_preedit
        self.entries: dict[ttk.Combobox, ttk.Entry] = {}
        self.queries: dict[ttk.Combobox, tk.StringVar] = {}
        self.active: ttk.Combobox | None = None
        self.results: list[str] = []
        self.listbox = tk.Listbox(root, height=1, takefocus=False, exportselection=False,
                                  activestyle="none", borderwidth=1, relief="solid")
        self.listbox.bind("<ButtonPress-1>", self.click_result)
        self.listbox.bind("<ButtonRelease-1>", lambda _: "break")
        root.bind("<ButtonPress-1>", self.dismiss_outside, add="+")

    def field(self, parent, variable, *, row, column=1, columnspan=1, pady=5, padx=0, values=()):
        line = ttk.Frame(parent, style=parent.cget("style"))
        line.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=padx, pady=pady)
        line.columnconfigure(0, weight=1)
        combo = ttk.Combobox(line, textvariable=variable, values=values, state="readonly", width=5)
        combo.grid(row=0, column=0, sticky="ew")
        ttk.Label(line, text="검색", style="SectionHint.TLabel").grid(row=0, column=1, padx=(5, 2))
        query = tk.StringVar(line)
        entry = ttk.Entry(line, textvariable=query, width=6)
        entry.grid(row=0, column=2)
        self.entries[combo] = entry
        self.queries[combo] = query

        def refresh(*_):
            if str(entry.tk.call("focus")) == str(entry):
                self.show(combo)

        query.trace_add("write", refresh)
        entry.bind("<KeyRelease>", refresh)
        entry.bind("<FocusOut>", lambda _: self.clear(combo))
        entry.bind("<Unmap>", lambda _: self.clear(combo))
        for key, delta in (("Down", 1), ("Up", -1), ("KP_Down", 1), ("KP_Up", -1)):
            entry.bind(f"<{key}>", lambda _, step=delta: self.move(combo, step))
        for key in ("Return", "KP_Enter"):
            entry.bind(f"<{key}>", lambda _: self.choose(combo))
        entry.bind("<Escape>", lambda _: self.cancel(combo))
        for key in ("<Control-f>", "<Control-F>"):
            combo.bind(key, lambda _: self.focus_search(combo))
        return combo

    def focus_search(self, combo):
        if not combo.instate(["disabled"]):
            self.entries[combo].focus_set()
        return "break"

    def clear(self, combo):
        query = self.queries[combo]
        if query.get():
            query.set("")
        self.hide(combo)

    def hide(self, combo):
        if self.active is combo:
            self.listbox.place_forget()
            self.active = None
            self.results = []

    def cancel(self, combo):
        combo.focus_set()
        self.clear(combo)
        return "break"

    def dismiss_outside(self, event):
        combo = self.active
        if combo and event.widget not in (self.entries[combo], self.listbox):
            self.clear(combo)

    def show(self, combo):
        entry = self.entries[combo]
        query = entry.get()
        preedit = self.read_preedit(entry.winfo_id())
        if preedit:
            index = entry.index("insert")
            query = query[:index] + preedit + query[index:]
        if not query.strip() or combo.instate(["disabled"]) or not entry.winfo_viewable():
            self.hide(combo)
            return
        results = catalog_matches(combo["values"], query)
        if self.active is combo and results == self.results:
            return
        self.active, self.results = combo, results
        box = self.listbox
        box.delete(0, "end")
        box.configure(font=combo.cget("font"), height=min(8, max(1, len(results))))
        box.insert("end", *(results or ["검색 결과 없음"]))
        if results:
            self.highlight(0)
        line = combo.master
        below = self.root.winfo_rooty() + self.root.winfo_height() - line.winfo_rooty() - line.winfo_height()
        above = below < box.winfo_reqheight() and line.winfo_rooty() - self.root.winfo_rooty() > below
        box.place(in_=line, relwidth=1, rely=0 if above else 1, anchor="sw" if above else "nw")
        box.lift()

    def highlight(self, index):
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)

    def move(self, combo, delta):
        self.commit_preedit(self.entries[combo])
        self.show(combo)
        if self.active is combo and self.results:
            index = self.listbox.index("active") + delta
            self.highlight(max(0, min(index, len(self.results) - 1)))
        return "break"

    def choose(self, combo):
        self.commit_preedit(self.entries[combo])
        self.show(combo)
        if self.active is combo and self.results:
            self.select(combo, self.results[self.listbox.index("active")])
        return "break"

    def click_result(self, event):
        index = self.listbox.nearest(event.y)
        if self.active and 0 <= index < len(self.results):
            combo, value = self.active, self.results[index]
            self.commit_preedit(self.entries[combo])
            self.select(combo, value)
        return "break"

    def select(self, combo, value):
        if combo.instate(["disabled"]) or value not in combo["values"]:
            self.clear(combo)
            return
        self.clear(combo)
        combo.set(value)
        combo.focus_set()
        combo.event_generate("<<ComboboxSelected>>")
