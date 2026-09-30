from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unit_print_manager import (  # noqa: E402
    DEFAULT_ABLE_ROOT,
    DEFAULT_APEX_ROOT,
    DEFAULT_PRINTER,
    DEFAULT_ROOT,
    DEFAULT_TRAINING_PACK_ROOT,
    MATERIAL_CATALOG_PATH,
    PrintManagerApp,
    catalog_exam_files,
    catalog_levels,
    catalog_rounds,
    catalog_subjects,
    find_blank_review_pdf,
    load_apex_catalog,
    load_unit_catalog,
    scan_able_catalog,
    scan_training_pack_catalog,
)
from unit_print_orders import OrderRow  # noqa: E402


class Variable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value


def row(source: str, row_number: int, **values: object) -> OrderRow:
    payload: dict[str, object] = {
        "row_number": row_number,
        "order_id": f"QA-{row_number}",
        "source": source,
        "subject": "",
        "level": "",
        "round_name": "",
        "document_kind": "교재",
        "mode": "",
        "start": None,
        "end": None,
        "selected_numbers": (),
        "copies": 1,
        "bundled": False,
        "printer_profile": "자동 감지",
        "file_paths": (),
        "note": "",
        "material_id": "",
        "student_name": "",
    }
    payload.update(values)
    return OrderRow(**payload)  # type: ignore[arg-type]


def main() -> int:
    app = PrintManagerApp.__new__(PrintManagerApp)
    app.catalog = load_unit_catalog()
    app.root_dir_var = Variable(str(DEFAULT_ROOT))
    app.printer_var = Variable(DEFAULT_PRINTER)
    app.apex_material_catalog = load_apex_catalog(DEFAULT_APEX_ROOT, MATERIAL_CATALOG_PATH)
    app.able_material_catalog = scan_able_catalog(DEFAULT_ABLE_ROOT)
    app.training_pack_material_catalog = scan_training_pack_catalog(DEFAULT_TRAINING_PACK_ROOT)

    rows: list[OrderRow] = []
    subjects = catalog_subjects(app.catalog)
    if subjects:
        subject = subjects[0]
        level = catalog_levels(app.catalog, subject)[0]
        round_name = catalog_rounds(app.catalog, subject, level)[0]
        files = catalog_exam_files(app.catalog, DEFAULT_ROOT, subject, level, round_name)
        rows.append(
            row(
                "단원평가",
                2,
                subject=subject,
                level=level,
                round_name=round_name,
                selected_numbers=(files[0].number,),
            )
        )

    next_row_number = 3
    for variant in ("APEX", "APEX Boost"):
        document = next(
            (
                item
                for item in app.apex_material_catalog.documents
                if item.variant == variant and item.document_kind == "교재" and item.lessons
            ),
            None,
        )
        if document is None:
            continue
        rows.append(
            row(
                document.variant,
                next_row_number,
                subject=document.subject,
                document_kind=document.document_kind,
                mode="차시 별",
                selected_numbers=(document.lessons[0].number,),
            )
        )
        next_row_number += 1

    able = next(
        (item for item in app.able_material_catalog.subjects if item.lesson_numbers and item.review_document),
        None,
    )
    if able is not None:
        rows.append(
            row(
                "A:ble",
                next_row_number,
                subject=able.subject,
                mode="차시별",
                selected_numbers=(able.lesson_numbers[0],),
            )
        )
        next_row_number += 1

    training = next(
        (item for item in app.training_pack_material_catalog.subjects if item.lesson_numbers),
        None,
    )
    if training is not None:
        rows.append(
            row(
                "트레이닝팩",
                next_row_number,
                subject=training.subject,
                mode="차시별",
                selected_numbers=(training.lesson_numbers[0],),
            )
        )
        next_row_number += 1

    review = find_blank_review_pdf()
    rows.append(row("백지리뷰", next_row_number))
    next_row_number += 1
    rows.append(row("PDF", next_row_number, file_paths=(review,), printer_profile="세로"))

    blocks, issues = app.resolve_order_csv_blocks(tuple(rows))
    if issues:
        for issue in issues:
            print(issue.format())
        return 1
    if len(blocks) != len(rows):
        print(f"block count mismatch: {len(blocks)} != {len(rows)}")
        return 1
    for block in blocks:
        missing = [item.path for item in block.files if not item.path.is_file()]
        if missing:
            print(f"missing generated files: {missing}")
            return 1
        print(block.summary)
    print(f"OK: {len(blocks)} order types resolved without print dispatch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
