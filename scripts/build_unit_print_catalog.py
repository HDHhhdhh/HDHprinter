from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unit_print_manager as app


ROOT = app.DEFAULT_ROOT
OUT = app.CATALOG_PATH


def main() -> None:
    catalog = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root_hint": str(ROOT),
        "subjects": [],
    }

    for subject in app.scan_subjects(ROOT):
        subject_info = {"name": subject, "levels": []}
        for level in app.scan_levels(ROOT, subject):
            level_info = {"name": level, "rounds": []}
            for round_name in app.scan_rounds(ROOT, subject, level):
                files = app.scan_exam_files(ROOT, subject, level, round_name)
                if not files:
                    continue
                round_info = {"name": round_name, "files": []}
                for item in files:
                    round_info["files"].append(
                        {
                            "number": item.number,
                            "name": item.name,
                            "filename": item.path.name,
                            "relative_path": item.path.relative_to(ROOT).as_posix(),
                        }
                    )
                level_info["rounds"].append(round_info)
            if level_info["rounds"]:
                subject_info["levels"].append(level_info)
        if subject_info["levels"]:
            catalog["subjects"].append(subject_info)

    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    total = app.count_catalog_files(catalog)
    print(f"Wrote {OUT} ({total} exam PDFs)")


if __name__ == "__main__":
    main()
