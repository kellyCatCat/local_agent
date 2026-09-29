"""把上传的 Excel / Markdown / CSV / 文本转换成给模型阅读的 markdown 文本。"""
from __future__ import annotations

import csv
import io
from pathlib import PurePath

from openpyxl import load_workbook

TEXT_EXTS = {".md", ".markdown", ".txt"}
EXCEL_EXTS = {".xlsx", ".xlsm"}
CSV_EXTS = {".csv"}
SUPPORTED_EXTS = TEXT_EXTS | EXCEL_EXTS | CSV_EXTS


class ParseError(ValueError):
    pass


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _cell(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("|", "\\|").replace("\n", "<br>")


def rows_to_markdown(rows: list[list]) -> str:
    """二维数组 -> markdown 表格；去掉全空行/全空列，第一行作表头。"""
    rows = [[_cell(v) for v in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return "（空表）"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def excel_to_markdown(data: bytes) -> str:
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:  # openpyxl 抛出的异常类型较多
        raise ParseError(f"无法解析 Excel：{e}") from e
    parts = []
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        # 合并单元格：把左上角的值填充到整个合并区域，避免子场景等列出现大片空白
        for rng in ws.merged_cells.ranges:
            v = ws.cell(rng.min_row, rng.min_col).value
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    if r - 1 < len(grid) and c - 1 < len(grid[r - 1]):
                        grid[r - 1][c - 1] = v
        if not any(any(v is not None and str(v).strip() for v in row) for row in grid):
            continue
        parts.append(f"### Sheet：{ws.title}\n\n{rows_to_markdown(grid)}")
    return "\n\n".join(parts) if parts else "（Excel 中没有内容）"


def parse_upload(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext in TEXT_EXTS:
        return _decode(data)
    if ext in EXCEL_EXTS:
        return excel_to_markdown(data)
    if ext in CSV_EXTS:
        return rows_to_markdown(list(csv.reader(io.StringIO(_decode(data)))))
    if ext == ".xls":
        raise ParseError("不支持旧版 .xls，请在 Excel 中另存为 .xlsx 后上传")
    raise ParseError(f"不支持的文件类型：{ext or filename}（支持 {', '.join(sorted(SUPPORTED_EXTS))}）")
