# PROJECT KNOWLEDGE BASE

**Updated:** 2026-10-12

## OVERVIEW

This workspace contains `빠른 인쇄기`, a Windows Python/Tkinter desktop app for printing assessment, textbook, fixed-form, and arbitrary PDFs from one editable queue.
Its current product contract is `빠른인쇄기_제품동작_정본.md`; operator instructions are in `unit_print_manager_README.md`. This file covers that app and its supporting modules, not the unrelated PDF/QR/HWP batch tools in the same workspace.

## STRUCTURE

```text
./
├── unit_print_bootstrap.py            # Stdlib startup checks, single-instance lease, redacted diagnostics
├── unit_print_instance.py             # Same-user Global Windows mutex, independent of installation path
├── unit_print_manager.py              # Active Tkinter UI, catalogs, queue editing, integration boundary
├── unit_print_theme.py                # Original workbench visual tokens and ttk state styles; no print logic
├── unit_print_typography.py           # Pixel-measured Korean label wrapping; separate display variables
├── unit_print_domain.py               # Print models, pure job/routing/name/OMR/A:ble rules
├── unit_print_execution.py            # PrintExecutionMixin and explicit execution-service dependencies
├── unit_print_paths.py                # Configurable local/bundled asset root and legacy compatibility
├── unit_print_source_paths.py         # Deduplicated Explorer folder opening and selection of registered queue PDFs
├── unit_print_catalog.json            # Built-in subject/level/round/file catalog, relative to root_dir
├── unit_print_materials.py            # APEX, A:ble, training/frequent-type catalogs and page assembly
├── unit_print_test_catalog.py         # Review, school-district, and Ilsan test catalogs
├── unit_print_gyopyeong.py             # 교평 기출 catalog
├── unit_print_selected_exams.py       # 기출엄선 catalog
├── unit_print_textbooks.py            # Ready/deferred textbook editions, lesson ranges, and asset checks
├── unit_print_textbook_catalog.json   # 42 verified textbook editions and their independent page ranges
├── unit_print_orders.py               # CSV parsing and validation
├── unit_print_server_catalog.py       # Stable material IDs and server CSV catalog
├── unit_print_keyboard.py             # Keyboard navigation state and actions
├── unit_print_text_shortcuts.py       # Focus-scoped Entry/Spinbox selection and bounded text undo/redo
├── unit_print_name_separator.py       # Portrait student-name separator generation
├── unit_print_pdf_printing.py          # PDF preflight, layouts, Sumatra settings
├── unit_print_pdf_metadata.py          # Disposable, signature-checked page-count cache for catalogs/UI
├── unit_print_resource_estimate.py     # Background A4/staple/time forecast and sensor-band checks
├── unit_print_eta.py                   # Classified physical timings, active-time deduction, completion clock
├── unit_print_notifications.py         # Deduplicated Windows fault/recovery/completion alerts
├── unit_print_printer_*.py             # Printer profiles, driver setup, and routing domain
├── unit_print_printer_binding.py      # Windows TCP/IP destinations versus Canon monitor identity
├── unit_print_retry.py                # Pending-job retry state
├── unit_print_progress.py             # Pure run/hold/error/completion display state
├── unit_print_preparation.py          # Ordered background PDF builds and bounded look-ahead
├── unit_print_queue_state.py          # Queue snapshots, waiting blocks, visible retry membership
├── unit_print_queue_render.py         # Visible-row geometry; full scroll extent remains independent
├── unit_print_queue_recovery.py       # Atomic per-session snapshots, leases, recovery consumption receipts
├── unit_print_recovery_history.py     # Conservative whole-block eligibility from durable dispatch evidence
├── unit_print_history.py              # Durable daily JSONL storage and history/CSV CLI
├── unit_print_history_events.py       # Queue/job snapshots, PDF fingerprints, app history callbacks
├── unit_print_manager_config.json     # Machine-local operator settings; queue is not restored
├── quick_printer_local_paths.json     # Machine-local release_root override; excluded from Git
├── 빠른인쇄기_제품동작_정본.md         # Approved product and UI behavior
├── unit_print_manager_README.md       # Operator-facing run/use notes
├── unit_print_manager_requirements.txt # Pinned ttkbootstrap, tkinterdnd2, pypdf, reportlab
├── run_unit_print_manager.cmd         # Windows launcher using pythonw
├── run_unit_print_manager_hidden.vbs  # Hidden-window launcher
├── tests/test_unit_print*.py           # App regression tests without live printing
├── sync_quick_printer_assets.cmd       # Pull new/changed PDF assets from the configured channel
└── scripts/                           # Catalog, release, sync, server CSV, and GUI verification tools
```

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| Launch behavior | `unit_print_bootstrap.main()`, CMD/VBS launchers, `unit_print_instance.py` | Python 3.12; one Global mutex per Windows user SID before manager import. Errors show a notice and redacted log. |
| Startup hang diagnostics | `unit_print_bootstrap.StartupTrace`, `run_application()` | Best-effort phase/elapsed/window-mapped records; distinguish slow catalogs from import errors. No operator fields. |
| UI layout | `PrintManagerApp.build_ui_reset()` in `unit_print_manager.py` | The single active UI builder; obsolete reference UI was removed. |
| GUI styling | `unit_print_theme.py`, `configure_style()` | Existing Tkinter/ttkbootstrap only. Original white header/panels, gray body and blue actions. Healthy idle/ready/completion are blue, progress green, queued waiting orange and faults red. Queue names are blue bold; subtab/subject black bold; details normal, with no `[일반]` marker. Entry/Combobox/Spinbox require class-scoped option-db fonts before construction. Preserve 62px cards, 8px gap/padding and normal/focus border dimensions. One-line titles may clip; small metadata belongs on the right. Source/license notes: `빠른인쇄기_GUI_디자인_참고.md`. |
| Korean text wrapping | `unit_print_typography.wrap_korean_text()`, `korean_label()`, `unit_print_toggle_help.py` | Keep words intact unless a word exceeds the available pixel width. Short popup/hover titles balance up to six lines; body labels reduce lonely final words when they fit. Explicit newlines remain; horizontal label whitespace collapses only in the private display value. Preserve original StringVars, logs, input text, one-line badges/buttons, and clipped queue titles. Coalesce source/resize callbacks at idle; clean up traces/bindings on destruction. Natural-width hover cards use `responsive=False` to avoid shrinking feedback. |
| Queue refresh/render | `refresh_queue_selection()`, `update_queue_view()`, `unit_print_queue_render.py` | Selection refresh skips history/totals; model mutations retain them. Paint only visible rows, but scroll/navigation must use the full scrollregion. |
| Asset roots | `unit_print_paths.resolve_release_root()`, `select_configured_root()` | Environment override, local path config, bundled data, then per-catalog legacy fallback. |
| Open queue PDF folders | `open_selected_block_source_folders()`, `unit_print_source_paths.py` | Right-click `원본 경로 열기`; use registered `block.files` even for generated range PDFs. Open each folder once and select all its existing target PDFs. Background filesystem/shell calls, ten-folder limit, no print/status/history/retry changes. |
| Catalog fallback | `load_unit_catalog`, `catalog_*`, `scan_*` helpers | Uses `unit_print_catalog.json` when present; scans folders otherwise. |
| Textbook catalog | `unit_print_textbooks.py`, `unit_print_textbook_catalog.json` | Only complete verified editions are selectable; any incomplete edition stays `pending` with no lesson ranges. |
| Queue editing | `add_current_block`, `add_custom_pdf_block`, `commit_selected_block_replacement`, `save_pending_queue_editor`, drag handlers | Bound editor drafts auto-save before leaving selection/workspace; failed validation retains input/selection. Same-block redraws preserve drafts; metadata-only saves reuse PDFs. Started/submitted blocks are protected; unstarted scheduled blocks may be deleted and name groups are repaired. |
| Preview/preflight | `update_preview()`, `unit_print_pdf_printing.py` | Calculates job order, copies, layouts, missing/invalid PDFs, and separators; preserve programmatic dry-run checks. |
| Print dispatch/retry | `unit_print_execution.PrintExecutionMixin`, `unit_print_retry.py` | Background sending, active-run appends, stop and retry; explicit services supplied by manager. Already sent blocks are excluded. |
| Queue state | `unit_print_queue_state.waiting_blocks()`, `editable_waiting_block_ids()`, `replace_unstarted_block_jobs()`, `retain_visible_retry_jobs()`, `QueueSnapshot` | Preserve block identity and completed/retry state across queue edits and undo. Live grouping/name edits atomically replace UI blocks and unclaimed jobs; dispatch claims override delayed badges. |
| Manual queue recovery | `show_queue_recovery()`, `restore_recovery_blocks()`, `unit_print_queue_recovery.py`, `unit_print_recovery_history.py` | Empty/idle queue only; source lease, fresh snapshot/history/consumption checks, preflight, required record and receipt precede commit. Never auto-print. |
| Printer destination verification | `ensure_device_printer_binding()`, `assert_device_binding_for_dispatch()`, `unit_print_printer_binding.py` | Validate actual body/name/separator/OMR TCP/IP targets before start/retry/append; retain approved names and monitor identity during dispatch. |
| Persistent history | `unit_print_history.PrintHistory`, `unit_print_history_events.PrintHistoryMixin` | Queue changes and print events; required history writes precede dispatch. |
| Sumatra printing | `sumatra_enqueue_pdfs()` | Default engine; shared prioritized preparation and next-two look-ahead live in `unit_print_preparation.py`. |
| Windows fallback | `shell_print_pdf()` | Uses ShellExecute `printto`; sends one copy at a time. |
| Printer queue polling | `get_print_queue_count()`, `wait_for_*()` | PowerShell `Get-PrintJob` controls pacing and final completion status. |
| Resource forecast | `unit_print_resource_estimate.py`, `unit_print_eta.py`, `schedule_resource_estimate()` | Dedicated cancellable worker for page counts, duplex/4-up sheets, staple sets, and supplies. ETA uses short/long/repeated workload timings, sheet-weighted long samples, actual dispatch count and a separate point estimate. Remove queue waiting with preceding exact device completion; reject held/external intervals and cold first short samples. Deduct only identified active printing time, bounded by submission and local completion observation. Cache repeated workload shapes per forecast. |
| Windows notifications | `unit_print_notifications.py`, `notify_operator()` | Transition-only error, recovery, and completion balloons; never notify on every poll. |
| Catalog rebuild | `scripts/build_unit_print_catalog.py` | Imports app scan helpers and writes `unit_print_catalog.json`. |
| Server CSV | `unit_print_orders.py`, `unit_print_server_catalog.py` | Resolve exact material IDs, student-name separators, and automatic OMR jobs. |
| Release/asset sync | `scripts/build_quick_printer_release.py`, `verify_quick_printer_release.py`, `quick_printer_asset_sync.py` | Full baseline plus manifest-based PDF updates; do not delete extra assets automatically. |
| Release fonts | `fonts/quick_printer_fonts.json`, `fonts/README.md`, `scripts/verify_quick_printer_fonts.py` | Bundle exact upstream-matching Pretendard v1.3.9 Regular/Bold/Medium/SemiBold TTF and OFL notice. Verify installed face hashes and actual Tk selection. Recipient installs these four files first; no commercial font copying or automatic conflicting-font replacement. `install_quick_printer.ps1 -SkipAssetSync` preserves the baseline during installation. Download and asset-sync Drive channels are separate. |
| Safe local asset copies | `scripts/quick_printer_safe_assets.py` | Verify size/SHA/pages in inherited-access staging; publish only a complete new directory. Preserve source and existing destinations. |

## CODE MAP

Search by symbol rather than keeping unstable line numbers here. Models and pure rules live in `unit_print_domain.py`; execution methods live in `unit_print_execution.py` and are inherited by the app.

| Symbol | Role |
|---|---|
| `ExamPdf` | Number/name/path value for one assessment PDF. |
| `PrintBlock` | Queue block with source, range/individual selection, copies, layout, printer route, and student name. |
| `PrintJob` | Expanded print unit used by the background worker. |
| `PrintManagerApp` | GUI, queue state, local settings, preview, and print orchestration. |
| `build_print_jobs()` | Expands blocks into order, bundled copies, name/general separators, and OMR. |
| `print_blocks()` | Sends jobs, updates state, preserves retry work, and watches the Windows print queue. |
| `PrintExecutionServices` | Immutable explicit callbacks/constants at the manager/execution boundary; no import back into manager. |
| `unit_print_bootstrap.main()` | Checks the startup environment and owns the instance lease throughout `run_application()`. |

## OPERATOR FLOW

1. Start with an empty queue and every copies input set to 1; the initial selection is `테스트 > 단원평가`.
   Starting a second copy under the same Windows user shows a notice without creating a queue or GUI, even from a different installation or Windows session. Other users/PCs and external print apps are outside this lock. An already-running pre-patch process must exit normally before the next launch gains protection.
2. Use the five main tabs: `테스트 / 교재자료 / 백지리뷰/OMR / PDF파일 / 대량주문서`. The test tabs are `단원평가 / 복습테스트 / 학군지기출 / 일산기출`; the last material tab is `교과서`, with nested `교과서 / 핵심문제 변형` tabs.
3. Select material and range/individual numbers, copies, and student name when applicable; add a block. Direct PDFs also support multiple-file drag-and-drop and a per-block printer route.
4. Import server or detailed order CSVs through `대량주문서` to create multiple validated blocks at once. Each imported batch is stably sorted by the NFC-normalized name-separator label in Korean Unicode order; blocks for the same student keep CSV-relative order, unnamed blocks follow named blocks, and pre-existing queue items are not reordered.
5. Edit, reorder, delete, undo/redo, or copy/paste queue blocks. Dragging any member of a multi-selection moves the selected blocks together in their existing relative order and keeps the moved group selected; releasing without a drag also preserves that selection. Pasted/duplicated blocks receive fresh history IDs and enter below the last selected block as waiting work; without selection, or during printing/retry, they enter at the tail. During printing, unstarted `예약`/`추가됨` blocks may be deleted; started/submitted blocks remain protected and deletion repairs the remaining name-separator groups.
   Keyboard commands are focus-scoped: Tab/Shift+Tab traverse fields, F6/Shift+F6 cycle builder/queue/editor, and Enter in editable queue fields saves after committing IME composition. Combobox Enter remains open/confirm. Text undo/redo must never invoke queue undo/redo. Queue Ctrl-navigation keeps selection while moving the cursor; Ctrl+Shift extends selection additively. Commit-time drag checks protect blocks if printing starts after the initial mouse press. Full operator shortcuts are in `빠른인쇄기_키보드_사용가이드.md`.
6. Check the queue before starting. Normal operation starts with dry-run off and duplex on; the active UI does not expose preview/duplex toggles. Preserve the internal dry-run path for verification.
   Queue right-click `묶기` groups original PDF references, not PDF pages: preserve selected queue/file order, put one block at the first selected position, and set `bundled=True`. Five one-file blocks with five copies each become separate PDF jobs `[1,2,3,4,5] * 5`; each PDF retains its own staple setting. Keep matching copies/name/effective destination/layout/orientation and existing print protections. Verify source materials through the existing background preparation service, then validate object identity, input values, queue position, status, and printer on the UI thread before commit. Undo/clear cancels pending preparation; stale completions never replace the queue. A:ble/APEX lesson PDF merging retains its separate approved behavior. Legacy groups that already contain one merged PDF cannot be automatically split; recreate them by selecting the original blocks. Do not automatically convert or reprint already-submitted work.
   During another block's printing, `묶기` and `이름 채우기` accept unstarted `대기`/`예약`/`추가됨` blocks. Any reserved member enrolls the entire group, including selected new waiting files, at the first selected reservation's dispatch position; waiting-only groups stay unscheduled. Name fill updates only blank targets; named donors may already be active/completed. Under `print_state_lock`, recheck worker claims, protect the started prefix, splice fresh jobs, and repair name separators in actual dispatch order. Retargeted jobs need fresh job IDs so cached worker candidates fail claim checks. Started/submitted/uncertain/retry targets remain protected, and undo/redo remain blocked during printing. Queue revision changes invalidate stale append/preparation plans.
7. The worker checks PDFs and printer profiles before sending. With Canon Remote UI configured, it keeps a tokenized rolling window of six submitted jobs by default (roughly one printing plus five waiting, configurable 1–10), waits for each token to appear in Canon's lightweight active list or job log before submitting the next app job, and replenishes one slot for each exact device completion. Full health, supplies, and completion snapshots continue on their own polling cycle. That device-acknowledgement barrier preserves app order across separate Windows queues targeting the same Canon without adding a full-status delay after every document. Errors stop replenishment; `해결 후 이어서` reconciles already submitted tokens before filling free slots. Without device monitoring, the legacy completion signal refers only to the Windows spooler. After stop or error, the main print action resumes retained unsent jobs before ordinary waiting blocks and must not resend submitted or uncertain jobs.

## DATA

- Asset-root precedence: `QUICK_PRINTER_RELEASE_ROOT` environment variable, then `quick_printer_local_paths.json`'s `release_root`, otherwise the app directory. Assets are under `<release_root>\data`.
- This operator's local C-drive copy is selected by local configuration; do not hardcode their username or copy path into portable code. A release uses its bundled `data` directory when no override is configured.
- Catalogs prefer their folder within the selected data root when it exists, with legacy C/D source paths retained for compatibility. A valid explicitly configured catalog root remains supported; inspect `select_configured_root()` before changing path behavior.
- Important relative folders: `단원평가`, `복습 테스트`, `학군지 기출`, `일산 기출`, `APEX`, `Able\렉쳐노트`, `기출엄선`, `트레이닝팩`, `빈출유형`, `교평 기출`, `교과서`, `백지리뷰`, and `OMR`.
- Textbook paths are relative to `data\교과서`. Catalog page ranges use the actual print PDFs, are 1-based and inclusive, and are independently verified for core-problem, problem, and solution documents. All 42 current editions are verified from original-backed files. Each core-problem PDF now starts with two cover pages: a full edition title and a QR linking to its uploaded Drive solution PDF. `core_cover_pages: 2` makes every selected core block print those pages once; its verified section and excluded-page boundaries are shifted by two. Core-problem sections map selected problem-part numbers to overlapping medium-unit pages plus the enclosing major-unit assessment; the source PDF order and section boundaries are explicit in `unit_print_textbook_catalog.json`. If any document or boundary becomes incomplete or uncertain, defer the whole edition as `pending` with `lessons: []`; never infer, partially register, repair, or silently skip it.
- The separate textbook `핵심문제 변형` subtab registers 15 additional PDFs. Select the same named lesson range or individual lesson numbers as the existing textbook UI. The verified per-edition section counts map each selected lesson to its medium-unit variant questions plus its enclosing major-unit assessment; each variant number N includes N-A/B/C. Print cover pages 1–2 once, then deduplicated original-order body pages. Shared pages can contain neighboring problem numbers. The variant metadata, section counts, and signatures live in `unit_print_textbook_catalog.json`; the release collector includes these assets.
- Catalog relative paths resolve under `root_dir`; changing `root_dir` keeps the built-in relative catalog.
- For Ilsan tests, explicit level-based problem PDFs supersede flat `subject/term/N회차.pdf` legacy files only within the same subject and term. Keep those baseline files on disk for asset integrity checks; the scanner excludes them from the active catalog and reports `excluded_legacy_fallback_count`.
- The 34 refreshed Ilsan 공수2/미적분1 midterm assets accept their registered `t_ilsan_past_...` server test IDs as order-CSV aliases. The binding is relative-path based in `unit_print_server_catalog.py`; resolved blocks retain the canonical `QT-ILSAN-...` material ID for history.
- Config persists operator paths, printer, Sumatra path, custom PDF defaults, queue pacing, separator flags, and engine. `save_current_config()` writes copies as 1, duplex as true, and `blocks` as `[]`; restarting never restores old print work.
- The fixed one-page separator asset is landscape `unit_print_separator_a4.pdf`; its centered `구분지` label leaves about 10% side margins.
- History stays beside the app at `output/unit_print_manager/history/YYYY-MM-DD.jsonl`, independent of the PDF data root. Dates and timestamp offsets follow the OS local timezone. Records include session/run/block/job IDs, student/order/material metadata, files, copies, printers, and event outcomes.
- Canon `printed_pdf` history fingerprints the exact dispatched spool-copy bytes. `prepared_pdf_path` separately identifies the build source. Spool-copy removal accepts only tokenized direct children of the owned device spool directory and refuses originals/symlinks/outside paths.
- History is an audit record, not persisted retry state: reopening still starts with an empty queue. Do not add automatic reprinting or date-based history deletion without a requested product change.
- Queue mutations save full metadata asynchronously under `output/unit_print_manager/recovery/session-UUID.json`. Empty/new sessions do not overwrite a previous backup. `큐 도구` and queue-focused Ctrl+S/Ctrl+Shift+O expose explicit save/recovery. Restore only whole blocks with no dispatch evidence; already submitted, uncertain, partially sent, or previously recovered blocks stay review-only. Source leases reject recovery from another live instance. Consumption sidecars are required proof, never reset when writing a new snapshot; missing/corrupt receipts or source history fail closed. Copies, layout, route, ordering and source metadata survive with fresh block IDs. PDF files are not copied by snapshots, so restore preflights all selected paths.
- Recovery snapshots and receipts contain student information and must stay out of Git and release bundles. Snapshot publication coalesces edits; selection navigation does not save. The dispatch proof uses the existing required `job_attempt` record, with no extra per-PDF recovery fsync.
- Page-count metadata is cached at `output/unit_print_manager/cache/pdf_page_counts.json`, bounded to 512 entries and invalidated by file signatures. It is disposable and excluded from releases; it does not replace print preflight. Queue history changes may use one ordered `record_many` batch with one fsync, while required print records remain individually synchronous.
- ETA timing samples use `device_monitor.eta_job_timing_samples` with a hashed monitor identity and at most 100 numeric sheet/copy/duration/duplex records. Legacy flat rates remain config-compatible but are not used for new forecasts. An optional local numeric `cache/eta_job_timing_bootstrap.json` is bounded to 64 KiB, checked against the monitor identity, imported once at startup, and excluded from releases. Never scan history on the UI thread or copy operator calibration into a general bundle. Record Canon start/end and queue-free service duration with the existing completion event; do not add per-PDF fsync. Final `eta_initial_error_seconds` is confirmation minus the first predicted deadline and needs the job counts to interpret appends/deletions.
- Asset pulls verify SHA-256 for base PDFs as well as overlays. Missing or modified base files block the update and must be restored from the verified baseline or repaired through the publisher. Hashing reads the base PDFs even when no download is needed.

## COMMANDS

```powershell
python -m pip install -r unit_print_manager_requirements.txt
python scripts/verify_quick_printer_environment.py
python -m py_compile unit_print_manager.py scripts/build_unit_print_catalog.py
python -m unittest discover -s tests -p "test_unit_print*.py"
python scripts/verify_quick_printer_server_integration.py
python -m unit_print_history --date 2026-09-05
python -m unit_print_history --date 2026-09-05 --csv output/print_history_2026-09-05.csv
```

Use `.venv\Scripts\python.exe` instead of `python` when using the app virtual environment. Catalog rebuild (`python scripts/build_unit_print_catalog.py`) writes metadata; run it only for an intended catalog update. GUI capture and launch are separate checks; neither is required for a documentation-only change.

## CONVENTIONS

- Keep Korean operator labels readable with Malgun Gothic and fixed workflow wording already used in README.
- Preserve the approved GUI's placement, dimensions, alignment, fonts, colors, and keyboard behavior outside the requested change. A successful screenshot capture alone does not establish absence of clipping or interaction regressions.
- Treat `unit_print_manager_config.json` as local operator state, not a stable fixture.
- Do not trigger live printing or driver dialogs during investigation without explicit current-task approval.
- Prefer SumatraPDF path validation through `find_sumatra_pdf()`; Windows `printto` is only the fallback.
- Preserve preview/dry-run behavior when touching print flow.
- Keep registered print PDFs, student/order data, local settings, and printer credentials out of Git and general release documents. Retain the tracked fixed separator asset and existing verified release policy.
- JSONL history and exported history CSVs contain student information. Keep them outside Git and release bundles; the module and CLI source belong in the release, the operator's records do not.

## GOTCHAS

- The GUI class remains large. Put new pure state, parsing, persistence, and print-domain logic in focused modules, keeping UI integration small.
- Every supported entry point delegates to bootstrap before manager imports. Do not call `run_application()` from a new launcher without the bootstrap lease. Startup checks are file-existence/package checks, not a scan or hash of all PDFs. Diagnostics omit raw exception messages and locals because they may contain student information or credentials.
- APEX analysis runs before the full GUI is built and uses relative path, size, and mtime cache signatures. Verified local PDF copies must preserve mtime; do not weaken signatures or retag a changed PDF to force a hit. A relocation needs unchanged bytes plus matching metadata. Keep a real withdrawn-root constructor/idle regression in addition to `__new__` worker fixtures. Startup traces indicate logical Tk mapping, not visual QA.
- `build_ui_reset()` is the active UI path; do not recreate an unused parallel UI implementation.
- A submitted or process-boundary-uncertain Sumatra job must advance out of retry eligibility before any later history write. Pre-dispatch history failure retains the unsent job. Confirmed and uncertain counts are distinct; test the production worker rather than an unused state wrapper.
- Preview worker exceptions must publish completion and release active/poll state. Do not immediately reschedule a failed PDF forever; explicit requests may retry and actual printing still requires preflight.
- Status maps use `id(block)` keys, so replacing a block requires moving status/detail state carefully.
- History UUIDs identify records across events; they do not replace in-memory `id(block)` status keys. Queue edits use `replaces_block_id` to connect old/new block records.
- `job_submitted` confirms a successful Sumatra call, not physical output. `run_finished` distinguishes `windows_queues_empty`, `queue_check_failed`, and `preview_only`; never infer printed paper from a bare attempted job.
- A required history-write failure stops further submission. After a confirmed send, advance the job index before recording `job_submitted`, so a disk failure cannot put the sent PDF back into the retry batch.
- The worker thread posts UI changes with `root.after`; direct Tk widget mutation from worker code is unsafe.
- `Get-Printer`/`Get-PrintJob`, SumatraPDF, ShellExecute `printto`, and `rundll32 printui.dll` are Windows-specific.
- Queue pacing depends on the selected printer name matching Windows exactly.
- Canon physical monitoring requires each actual print destination to have a verified local Standard TCP/IP port matching the monitor host. WSD, USB, shared/unknown ports cannot establish this identity. Read Windows inventory once per start/retry/append, not once per document. Freeze monitor identity and approved queue names during the run; this does not continuously detect external Windows port edits.
- Canon portal scripts contain every possible status sentence. Health parsing must use the current `prt_status`/`errCodeTbl` values or rendered text, never the complete script body. Job pages require the `/sysmonitor` navigation initializer and may return a login form when the session expires.
- Physically verified A4 cassette-open codes are only `0x01180102` and `0x01180202`, with observed portal status `0x20`/`0x21`. Treat them as paper loading and continue dispatch only when another closed, identified A4 cassette has a known positive sensor band. Open-drawer 0% is not proof of exhaustion; both open or unavailable/unknown alternate supply stays held. Mixed/unknown codes, visible faults, and other status bits remain blocking. Do not generalize the firmware mask to untested feed positions or bypass token acknowledgement/completion.
- Canon Remote UI monitoring is read-only and uses Windows Credential Manager for optional Department ID/PIN persistence. Never write the PIN to config, history, logs, Git, or release files. Paper levels are sensor bands, not exact sheet counts; resource forecasts may convert only known standard drawers into a conservative possible range.
