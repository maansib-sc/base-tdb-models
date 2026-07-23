
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import List, Optional
from ..base.base import make_id, RunModel
from .paragraph import ParagraphModel, build_comment_text
from ...placeholders.placeholder import PlaceholderModel


class _HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[dict]] = []
        self._current_row = None
        self._current_cell = None
        self._buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "tr":
            self._current_row = []

        elif tag in ("td", "th"):
            self._current_cell = {
                "text": "",
                "rowspan": int(attrs.get("rowspan", 1)),
                "colspan": int(attrs.get("colspan", 1)),
            }
            self._buffer = ""

    def handle_data(self, data):
        if self._current_cell is not None:
            self._buffer += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._current_cell:
            self._current_cell["text"] = self._buffer.strip()
            self._current_row.append(self._current_cell)
            self._current_cell = None
            self._buffer = ""

        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None


class HeaderOrientation(str, Enum):
    NONE = "none"
    ROW = "row"
    COLUMN = "column"
    BOTH = "both"


def cell_to_text(cell: Optional["TableCellModel"]) -> str:
    return cell.to_text(strip=True) if cell is not None else ""


@dataclass
class TableCellView:
    row: int
    col: int
    text: str
    row_header: str = ""
    col_header: str = ""
    id: Optional[str] = None


@dataclass
class TableRowView:
    index: int
    header: str
    cells: List[TableCellView] = field(default_factory=list)

    def to_text(self) -> str:
        """Return self-contained table rows."""
        parts = [
            f"{c.col_header}: {c.text}"
            if c.col_header and c.col_header != self.header
            else c.text
            for c in self.cells
        ]

        if self.header and parts:
            return f"{self.header} | " + " | ".join(parts)

        return self.header or " | ".join(parts)


@dataclass
class TableCellModel:
    paragraphs: List[ParagraphModel] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    type: str = "TableCell"
    id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "TableCellModel":
        data["paragraphs"] = [
            ParagraphModel.from_dict(p)
            for p in data.get("paragraphs", [])
        ]

        return cls(**data)

    def assign_ids(self, parent_id: str, row: int, col: int):
        self.id = f"{parent_id}:row::{row}:cell::{col}"

        for i, para in enumerate(self.paragraphs):
            para.assign_ids(self.id, i)

    def to_text(self, mode="full", strip: bool = False) -> str:
        text = "\n".join(
            p.to_text(mode) for p in self.paragraphs if p is not None
        )
        return text.strip() if strip else text

    def to_html(self) -> str:
        attrs = []

        if self.colspan and self.colspan > 1:
            attrs.append(f'colspan="{self.colspan}"')

        if self.rowspan and self.rowspan > 1:
            attrs.append(f'rowspan="{self.rowspan}"')

        attr_str = " " + " ".join(attrs) if attrs else ""

        inner_html = "".join(
            p.to_html() for p in self.paragraphs if p is not None
        )

        if not inner_html:
            inner_html = "&nbsp;"

        return f"<td{attr_str}>{inner_html}</td>"

    @staticmethod
    def apply_placeholder(cell: "TableCellModel", ph: PlaceholderModel) -> bool:
        if not ph.replaced_text:
            return False

        comment_text = build_comment_text(ph)

        for para in cell.paragraphs:
            full_text = para.to_text(mode="full")
            if ph.text in full_text:
                para.runs = RunModel.replace_text(
                    para.runs, ph.text, ph.replaced_text,
                    comment_text=comment_text)
                return True

        return False

    def apply_deleted_placeholder(self, ph: PlaceholderModel) -> bool:
        for para in self.paragraphs:
            full_text = para.to_text(mode="full")
            if ph.text in full_text:
                para.runs = RunModel.drop_text(para.runs, ph.text)
                return True

        return False


@dataclass
class TableModel:
    rows: List[List[TableCellModel]] = field(default_factory=list)
    type: str = "Table"
    id: Optional[str] = None

    parent_ref_id: Optional[str] = None
    caption_ref_id: Optional[str] = None
    page: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> "TableModel":
        data["rows"] = [
            [
                TableCellModel.from_dict(cell)
                for cell in row
            ]
            for row in data.get("rows", [])
        ]

        return cls(**data)

    def assign_ids(self, parent_id: str, index: int):
        self.id = make_id(parent_id, "table", index)

        for r_idx, row in enumerate(self.rows):
            for c_idx, cell in enumerate(row):
                if cell:
                    cell.assign_ids(self.id, r_idx, c_idx)

    def get_col_count(self) -> int:
        return len(self.rows[0])

    def get_row_count(self) -> int:
        return len(self.rows)

    def get_body_count(self) -> int:
        return len(self.get_rows(mode="body"))

    def get_type(self) -> str:
        col_count = self.get_col_count()
        colspan_count = sum(
            (cell.colspan for cell in self.rows[0][:2] if cell))
        if col_count == 1:
            return "Layout"
        if col_count-colspan_count == 0:
            return "Keyvalue"

        return "Unknown"

    def to_text(self, mode="full") -> str:
        lines = []

        for row in self.rows:
            if not row:
                lines.append("")
                continue

            cell_texts = []
            for cell in row:
                if cell is None:
                    cell_texts.append("")
                else:
                    cell_texts.append(cell.to_text(mode))

            lines.append("\t".join(cell_texts))

        return "\n".join(lines)

    def to_html(self, mode="full", row=None) -> str:
        """
        mode options:
            - "full": entire table
            - "header": top rows until max rowspan in first row
            - "rows": all rows AFTER header
            - "row": single row AFTER header (requires row=int, relative to body rows)
            - "row_with_header": single row AFTER header + header rows
        """
        if not self.rows:
            return "<table></table>"

        header_rows = self.get_rows(mode="header")
        body_rows = self.get_rows(mode="body")

        # Select rows based on mode
        if mode == "full":
            render_rows = self.rows
        elif mode == "header":
            render_rows = header_rows
        elif mode == "rows":
            render_rows = body_rows
        elif mode == "row":
            if row is None or row < 0 or row >= len(body_rows):
                return "<table></table>"
            render_rows = [body_rows[row]]
        elif mode == "row_with_header":
            if row is None or row < 0 or row >= len(body_rows):
                return "<table></table>"
            render_rows = header_rows + [body_rows[row]]
        else:
            return "<table></table>"

        html_rows = []
        rowspan_tracker = {}

        for r_idx, row_cells in enumerate(render_rows):
            html_cells = []
            c_idx = 0  # actual column index
            s_cell = 1

            for cell_model in row_cells:
                if s_cell > 1:
                    s_cell -= 1
                    continue
                if c_idx in rowspan_tracker and r_idx in rowspan_tracker[c_idx]:
                    c_idx += 1
                    continue

                if cell_model is None:
                    # Header tag if part of header rows
                    tag = "th" if r_idx < len(header_rows) else "td"
                    html_cells.append(f"<{tag}>&nbsp;</{tag}>")
                    c_idx += 1
                    continue

                s_cell = cell_model.colspan
                rowspan_tracker[c_idx] = [i for i in range(cell_model.rowspan)]

                attrs = []
                if cell_model.colspan > 1:
                    attrs.append(f'colspan="{cell_model.colspan}"')
                if cell_model.rowspan > 1:
                    attrs.append(f'rowspan="{cell_model.rowspan}"')
                attr_str = " " + " ".join(attrs) if attrs else ""

                inner_html = "".join(p.to_html()
                                     for p in cell_model.paragraphs if p)
                if not inner_html:
                    inner_html = "&nbsp;"

                tag = "th" if r_idx < len(header_rows) else "td"
                html_cells.append(f"<{tag}{attr_str}>{inner_html}</{tag}>")

                c_idx += cell_model.colspan

            html_rows.append(f"<tr>{''.join(html_cells)}</tr>")

        return f"<table>{''.join(html_rows)}</table>"

    def get_rows(self, mode="all") -> List[List[TableCellModel]]:
        """
        mode options:
            - "all": all rows
            - "header": top rows until max rowspan in first row
            - "body": all rows AFTER header
        """
        if not self.rows:
            return []

        if self.get_type() == "Keyvalue":
            header_row = []
            body_row = []
            for row in self.rows:
                header_row.append(row[0])
                body_row.append(row[1])
            header_rows = [header_row]
            body_rows = [body_row]
        else:
            max_header_rows = max(
                (cell.rowspan for cell in self.rows[0] if cell), default=1)
            header_rows = self.rows[:max_header_rows]
            body_rows = self.rows[max_header_rows:]

        if mode == "all":
            return self.rows
        elif mode == "header":
            return header_rows
        elif mode == "body":
            return body_rows
        else:
            return []

    def get_col_header(self, col: int, format="text", mode="drop"):
        header_path = []

        if col > len(self.rows[0]) - 1:
            return header_path

        max_header_rows = max(
            (cell.rowspan for cell in self.rows[0] if cell), default=1)

        for r in range(max_header_rows):
            if format == "text":
                header_path.append(self.rows[r][col].to_text(mode))
            if format == "html":
                header_path.append(self.rows[r][col].to_html(mode))

        return list(set(header_path))

    def get_row_header(self, row: int, format="text", mode="drop"):

        if row > len(self.rows) - 1:
            return []

        if format == "text":
            return [self.rows[row][0].to_text(mode)]
        if format == "html":
            return [self.rows[row][0].to_html(mode)]

    def get_header(self, row: int, col: int, format="text", mode="drop"):
        if self.get_type() == "Keyvalue":
            return self.get_row_header(row, format, mode)
        return self.get_col_header(col, format, mode)

    def header_orientation(self) -> HeaderOrientation:
        table_type = self.get_type()

        if table_type == "Layout":
            return HeaderOrientation.NONE
        if table_type == "Keyvalue":
            return HeaderOrientation.ROW
        return HeaderOrientation.BOTH

    def header_row_count(self) -> int:
        if self.header_orientation() not in (
            HeaderOrientation.COLUMN,
            HeaderOrientation.BOTH,
        ):
            return 0
        if not self.rows or not self.rows[0]:
            return 1
        return max((cell.rowspan for cell in self.rows[0] if cell), default=1)

    def column_headers(self) -> dict:
        headers: dict = {}

        for row in self.rows[: self.header_row_count()]:
            for col, cell in enumerate(row):
                text = cell_to_text(cell)
                if not text:
                    continue
                headers[col] = (
                    f"{headers[col]} {text}".strip() if col in headers else text
                )

        return headers

    def row_headers(self) -> dict:
        return {
            row_idx: cell_to_text(row[0])
            for row_idx, row in enumerate(self.rows)
            if row
        }

    def normalized_rows(self) -> List[TableRowView]:
        if not self.rows:
            return []

        has_row_header = self.header_orientation() in (
            HeaderOrientation.ROW,
            HeaderOrientation.BOTH,
        )

        header_count = self.header_row_count()
        col_headers = self.column_headers()
        first_col = 1 if has_row_header else 0

        views: List[TableRowView] = []

        for row_idx, row in enumerate(self.rows):
            if not row or row_idx < header_count:
                continue

            row_header = cell_to_text(row[0]) if has_row_header else ""

            cells = [
                TableCellView(
                    row=row_idx,
                    col=col,
                    text=cell_to_text(cell),
                    row_header=row_header,
                    col_header=col_headers.get(col, ""),
                    id=getattr(cell, "id", None),
                )
                for col, cell in enumerate(row)
                if col >= first_col and cell_to_text(cell)
            ]

            if row_header or cells:
                views.append(
                    TableRowView(index=row_idx, header=row_header, cells=cells)
                )

        return views

    @staticmethod
    def from_html_or_text(content: str) -> "TableModel":
        content = content.strip()

        if not content:
            return TableModel(rows=[])

        # -------- HTML TABLE --------
        if "<table" in content.lower():
            return TableModel._from_html(content)

        # -------- TSV / CSV / PIPE --------
        return TableModel._from_delimited_text(content)

    @staticmethod
    def _from_html(html: str) -> "TableModel":
        parser = _HTMLTableParser()
        parser.feed(html)

        rows: List[List[TableCellModel]] = []

        for row in parser.rows:
            table_row = []
            for cell in row:
                para = ParagraphModel(
                    style=None,
                    runs=[RunModel.from_text(cell["text"])]
                )

                table_row.append(
                    TableCellModel(
                        paragraphs=[para],
                        rowspan=cell["rowspan"],
                        colspan=cell["colspan"],
                    )
                )
            rows.append(table_row)

        return TableModel(rows=rows)

    @staticmethod
    def _from_delimited_text(text: str) -> "TableModel":
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return TableModel(rows=[])

        delimiter = TableModel._detect_delimiter(lines[0])

        rows: List[List[TableCellModel]] = []

        for line in lines:
            parts = [p.strip() for p in line.split(delimiter)]

            table_row = []
            for part in parts:
                para = ParagraphModel(
                    style=None,
                    runs=[RunModel.from_text(part)]
                )
                table_row.append(
                    TableCellModel(paragraphs=[para])
                )

            rows.append(table_row)

        return TableModel(rows=rows)

    @staticmethod
    def _detect_delimiter(line: str) -> str:
        if "\t" in line:
            return "\t"
        if "|" in line:
            return "|"
        return ","  # fallback
