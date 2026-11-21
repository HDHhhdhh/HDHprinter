"""Catalog search uses the real Tk widgets without a printer or operator data."""
import tkinter as tk
from tkinter import ttk
import unicodedata
import unittest

from unit_print_catalog_search import CatalogSearch, catalog_matches


class CatalogSearchTests(unittest.TestCase):
    def test_korean_initials_literal_text_and_mixed_input(self):
        subjects = ["공통수학1", "미적분1", "미적분2", "기하"]
        for query in ("ㅁㅈ", "ᄆᄌ", "미ㅈ", "미적", unicodedata.normalize("NFD", "미적")):
            self.assertEqual(subjects[1:3], catalog_matches(subjects, query))
        self.assertEqual(["미적분2"], catalog_matches(subjects, "ㅁㅈ 2"))
        self.assertEqual(["01. 다항식의 연산"], catalog_matches(["01. 다항식의 연산"], "ㅇㅅ"))
        self.assertEqual(["Lv.3"], catalog_matches(["Lv.3", "Lv.4"], "lv.3"))
        self.assertEqual([], catalog_matches(subjects, ".*"))

    def test_select_cancel_and_refresh_against_current_catalog(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        page = ttk.Frame(root)
        page.pack(fill="x")
        root.geometry("540x300")
        subject = tk.StringVar(root, "공통수학1")
        level = tk.StringVar(root, "Lv.1")
        search = CatalogSearch(root, read_preedit=lambda _: "", commit_preedit=lambda _: None)
        combo = search.field(page, subject, row=0, values=("공통수학1", "미적분1", "미적분2"))
        dependent = search.field(page, level, row=1, values=("Lv.1",))
        changes = []

        def changed(_):
            changes.append(subject.get())
            dependent.configure(values=("Lv.3", "Lv.4"))

        combo.bind("<<ComboboxSelected>>", changed)
        root.update()
        entry = search.entries[combo]
        entry.focus_force()
        root.update()
        entry.insert(0, "ㅁㅈ")
        root.update()
        self.assertEqual(("미적분1", "미적분2"), search.listbox.get(0, "end"))
        self.assertTrue(search.listbox.winfo_viewable())
        self.assertEqual("공통수학1", subject.get())
        entry.event_generate("<Down>")
        entry.event_generate("<Return>")
        root.update()
        self.assertEqual(["미적분2"], changes)
        self.assertFalse(search.listbox.winfo_viewable())
        self.assertEqual(3, len(combo["values"]))
        field = search.entries[dependent]
        field.focus_force()
        root.update()
        field.insert(0, "9")
        self.assertEqual(("검색 결과 없음",), search.listbox.get(0, "end"))
        field.event_generate("<Return>")
        self.assertEqual("Lv.1", level.get())
        field.delete(0, "end")
        field.insert(0, "4")
        self.assertEqual(["Lv.4"], search.results)
        field.event_generate("<Escape>")
        root.update()
        self.assertEqual("Lv.1", level.get())
        self.assertEqual("", field.get())
        field.focus_force()
        root.update()
        field.insert(0, "3")
        root.update()
        search.listbox.event_generate("<ButtonPress-1>", x=4, y=4)
        root.update()
        self.assertEqual("Lv.3", level.get())
