

import re
import hashlib
import zipfile
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from smart_slugify import slugify

from . import table_context
from .indexes.index import FileIndexModel, IndexItem, IndexType
from .layouts.layout import HeaderModel
from .layouts.layout import FooterModel
from .layouts.layout import LayoutModel
from .elements.primitive.table import TableModel
from .elements.primitive.table import TableCellModel
from .elements.primitive.paragraph import ParagraphModel
from .placeholders.placeholder import PlaceholderModel
from .placeholders.placeholder import PlaceholderStatus
from .mutations import ElementReplacement
from .resolver import resolve_structural_replacement
from ..utils.dataclass import from_dict_safe


def get_doc_uid(id: str) -> str:
    match = re.match(r'^doc::([^:]+)', id)
    return match.group(1)


@dataclass
class DocumentModel:
    layouts: List[LayoutModel] = field(default_factory=list)
    type: str = "Document"
    id: Optional[str] = None
    filename: Optional[str] = None

    _element_index: Dict[str, object] = field(
        default_factory=dict, init=False, repr=False)
    _paragraph_index: Dict[str, ParagraphModel] = field(
        default_factory=dict, init=False, repr=False)
    _paragraph_order: List[str] = field(
        default_factory=list, init=False, repr=False)
    _lead_in_index: Dict[str, str] = field(
        default_factory=dict, init=False, repr=False)

    @staticmethod
    def make_id(doc_uid: str) -> str:
        return f"doc::{slugify(doc_uid)}"

    @staticmethod
    def make_uid(io_buffer, length: int = 8) -> str:
        io_buffer.seek(0)
        header = io_buffer.read(8)
        io_buffer.seek(0)

        if header.startswith(b"PK"):
            try:
                return DocumentModel._stable_docx_hash(io_buffer)[:length]
            except Exception:
                pass

        data = io_buffer.read()
        io_buffer.seek(0)
        return hashlib.sha256(data).hexdigest()[:length]

    @staticmethod
    def _stable_docx_hash(io_buffer) -> str:
        io_buffer.seek(0)
        h = hashlib.sha256()

        with zipfile.ZipFile(io_buffer, "r") as z:
            for name in sorted(z.namelist()):
                if not name.startswith("word/"):
                    continue
                if not name.endswith(".xml"):
                    continue

                h.update(name.encode("utf-8"))
                h.update(z.read(name))

        io_buffer.seek(0)
        return h.hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentModel":
        data["layouts"] = [
            LayoutModel.from_dict(layout)
            for layout in data.get("layouts", [])
        ]

        return from_dict_safe(cls, data)

    def iter_elements(self):
        for layout in self.layouts:
            for elem in layout.elements:
                yield elem

    def iter_elements_chain(self, non_empty: bool = True):
        elements = [
            elem
            for layout in self.layouts
            for elem in layout.elements
        ]

        if not elements:
            return

        if not non_empty:
            prev_elem = None
            for i, current in enumerate(elements):
                next_elem = elements[i + 1] if i + 1 < len(elements) else None
                yield (prev_elem, current, next_elem)
                prev_elem = current
            return

        # -------- helper --------
        def is_non_empty(elem) -> bool:
            if not hasattr(elem, "to_text"):
                return True
            text = elem.to_text()
            return bool(text and text.strip())

        # -------- Pass 1: build next_non_empty list (minimal state) --------
        next_list = [None] * len(elements)
        next_seen = None

        for i in range(len(elements) - 1, -1, -1):
            next_list[i] = next_seen
            if is_non_empty(elements[i]):
                next_seen = elements[i]

        # -------- Pass 2: forward yield with rolling prev --------
        prev_seen = None

        for i, current in enumerate(elements):
            yield (prev_seen, current, next_list[i])

            if is_non_empty(current):
                prev_seen = current

    def _index_element(self, elem):
        if getattr(elem, "id", None):
            self._element_index[elem.id] = elem

    def _build_element_index(self):
        self._element_index.clear()

        for layout in self.layouts:
            self._index_element(layout)

            if layout.header:
                self._index_element(layout.header)

            if layout.footer:
                self._index_element(layout.footer)

            for elem in layout.elements:
                self._index_element(elem)

                # ---- TABLE DEEP INDEX ----
                if isinstance(elem, TableModel):
                    for row in elem.rows:
                        for cell in row:
                            if not cell:
                                continue
                            self._index_element(cell)

                            for para in cell.paragraphs:
                                self._index_element(para)
                                for run in para.runs:
                                    self._index_element(run)

                # ---- PARAGRAPH DEEP INDEX ----
                if isinstance(elem, ParagraphModel):
                    for run in elem.runs:
                        self._index_element(run)

    def _build_paragraph_index(self):
        self._paragraph_index.clear()
        self._paragraph_order.clear()
        for elem in self.iter_elements():
            if isinstance(elem, ParagraphModel):
                self._paragraph_index[elem.id] = elem
                self._paragraph_order.append(elem.id)

    def get_next_paragraph_text(self, para_id: str) -> Optional[str]:
        if not self._paragraph_order:
            self._build_paragraph_index()
        try:
            idx = self._paragraph_order.index(para_id)
        except ValueError:
            return None
        for next_id in self._paragraph_order[idx + 1:]:
            next_para = self._paragraph_index[next_id]
            if getattr(next_para, "is_heading", False) or getattr(next_para, "is_caption", False):
                return None
            text = next_para.to_text(mode="full")
            if text:
                return text.strip()
        return None

    def get_prev_paragraph_text(self, para_id: str) -> Optional[str]:
        if not self._paragraph_order:
            self._build_paragraph_index()
        try:
            idx = self._paragraph_order.index(para_id)
        except ValueError:
            return None
        for prev_id in reversed(self._paragraph_order[:idx]):
            prev_para = self._paragraph_index[prev_id]
            if getattr(prev_para, "is_heading", False) or getattr(prev_para, "is_caption", False):
                return None
            text = prev_para.to_text(mode="full")
            if text:
                return text.strip()
        return None

    def invalidate_paragraph_index(self):
        self._paragraph_index.clear()
        self._paragraph_order.clear()

    def get_element_by_id(self, element_id: str):
        if not self._element_index:
            self._build_element_index()

        return self._element_index.get(element_id)

    def invalidate_index(self):
        self._element_index.clear()
        self._lead_in_index.clear()

    def assign_ids(self, doc_index: int = 0):
        self.id = f"doc::{doc_index}"

        for i, layout in enumerate(self.layouts):
            layout.assign_ids(self.id, i)

    def build_hierarchy(self):
        # Stack of last seen heading per level
        heading_stack: dict[int, str] = {}

        last_heading_id: Optional[str] = None
        last_caption_id: Optional[str] = None

        for prev_elem, elem, next_elem in self.iter_elements_chain():

            # -------------------------
            # PARAGRAPHS
            # -------------------------
            if isinstance(elem, ParagraphModel):

                kind, level = elem.style.classify_style()

                # -------- CAPTION --------
                if kind == "caption" or (elem.to_text().lower().startswith(("table ")) and isinstance(next_elem, TableModel)):
                    elem.is_caption = True
                    elem.parent_ref_id = last_heading_id
                    last_caption_id = elem.id
                    if kind != "caption":
                        elem.style.name = "caption"
                    continue

                # -------- HEADING --------
                if kind == "heading" and level:
                    elem.is_heading = True
                    elem.heading_level = level

                    # Parent = previous lower-level heading
                    parent = None
                    for l in range(level - 1, 0, -1):
                        if l in heading_stack:
                            parent = heading_stack[l]
                            break

                    elem.parent_ref_id = parent

                    # Update stack
                    heading_stack[level] = elem.id

                    # Clear deeper levels
                    for l in list(heading_stack.keys()):
                        if l > level:
                            del heading_stack[l]

                    last_heading_id = elem.id
                    last_caption_id = None
                    continue

                intent = elem.classify_intent()
                if intent == "instruction":
                    elem.is_instruction = True
                if intent == "example":
                    elem.is_example = True

                # -------- NORMAL PARA --------
                elem.parent_ref_id = last_heading_id
                last_caption_id = None
                continue

            # -------------------------
            # TABLES
            # -------------------------
            if isinstance(elem, TableModel):
                elem.parent_ref_id = last_heading_id

                if last_caption_id:
                    elem.caption_ref_id = last_caption_id

                last_caption_id = None

                for row in elem.rows:
                    for cell in row:
                        for para in cell.paragraphs:
                            intent = para.classify_intent()
                            if intent == "instruction":
                                para.is_instruction = True
                            if intent == "example":
                                para.is_example = True
                continue

    def get_headings(self, format="text"):
        def is_heading(p: ParagraphModel):
            return bool(p and (p.is_heading or p.heading_level is not None))

        def get_level(p: ParagraphModel):
            return p.heading_level if p.heading_level is not None else 0

        def render(p: ParagraphModel):
            return p.to_html() if format == "html" else p.to_text()

        headings = []

        for layout in self.layouts:
            for elem in layout.elements:
                if isinstance(elem, ParagraphModel) and is_heading(elem):
                    headings.append({
                        "id": elem.id,
                        "heading": render(elem),
                        "level": get_level(elem),
                    })

        return headings

    def get_heading_content(
        self,
        heading_id: str,
        include_captions=False,
        include_tables=False,
        include_subheading=False,
        format="text",
    ):
        def is_heading(p: ParagraphModel):
            return bool(p and (p.is_heading or p.heading_level is not None))

        def get_level(p: ParagraphModel):
            return p.heading_level if p.heading_level is not None else 0

        def render(elem):
            return elem.to_html() if format == "html" else elem.to_text()

        heading_elem = self.get_element_by_id(heading_id)
        if not heading_elem or not isinstance(heading_elem, ParagraphModel):
            return None

        # Flatten document (layout-aware)
        flat_elements = []
        for layout in self.layouts:
            for elem in layout.elements:
                flat_elements.append(elem)

        # Locate heading position
        try:
            start_index = next(
                i for i, e in enumerate(flat_elements) if e.id == heading_id
            )
        except StopIteration:
            return None

        start_level = get_level(heading_elem)

        section = {
            "heading": render(heading_elem),
            "level": start_level,
            "content": [],
        }

        i = start_index + 1

        # Collect content
        while i < len(flat_elements):
            elem = flat_elements[i]

            # HARD STOP at any heading unless include_subheading=True
            if isinstance(elem, ParagraphModel) and is_heading(elem):
                if not include_subheading:
                    break
                elem_level = get_level(elem)
                if elem_level <= start_level:
                    break
            # Caption handling
            if isinstance(elem, ParagraphModel) and elem.is_caption:
                if include_captions:
                    section["content"].append(render(elem))
                i += 1
                continue

            if isinstance(elem, TableModel) and include_tables:
                section["content"].append(render(elem))

            if isinstance(elem, ParagraphModel):
                section["content"].append(render(elem))

            i += 1

        return section

    def get_heading_details(
        self,
        heading_id: str,
        mode: str = "full",
        format: str = "text",
    ):

        def is_heading(p: ParagraphModel) -> bool:
            return bool(p and (p.is_heading or p.heading_level is not None))

        def get_level(p: ParagraphModel) -> int:
            return p.heading_level if p.heading_level is not None else 0

        def render(p: ParagraphModel):
            return p.to_html() if format == "html" else p.to_text()

        heading = self.get_element_by_id(heading_id)
        if not heading or not isinstance(heading, ParagraphModel) or not is_heading(heading):
            return None

        headings = []
        for layout in self.layouts:
            for elem in layout.elements:
                if isinstance(elem, ParagraphModel) and is_heading(elem):
                    headings.append(elem)

        stack = []
        for h in headings:
            while stack and get_level(stack[-1]) >= get_level(h):
                stack.pop()

            h.parent_ref_id = stack[-1].id if stack else None
            stack.append(h)

        parent = (
            self.get_element_by_id(heading.parent_ref_id)
            if heading.parent_ref_id
            else None
        )

        siblings = [
            h for h in headings
            if h.parent_ref_id == heading.parent_ref_id
        ]

        childrens = [
            h for h in headings
            if h.parent_ref_id == heading.id
        ]

        position = next(
            (i for i, h in enumerate(siblings) if h.id == heading.id),
            None
        )

        if mode == "position":
            return position

        if mode == "siblings":
            return [
                {
                    "id": h.id,
                    "heading": render(h),
                    "level": get_level(h),
                }
                for h in siblings
            ]

        if mode == "childrens":
            return [
                {
                    "id": h.id,
                    "heading": render(h),
                    "level": get_level(h),
                }
                for h in childrens
            ]

        if mode == "parent":
            if not parent:
                return None
            return {
                "id": parent.id,
                "heading": render(parent),
                "level": get_level(parent),
            }

        if mode == "full":
            return {
                "id": heading.id,
                "heading": render(heading),
                "level": get_level(heading),
                "position": position,
                "parent": (
                    {
                        "id": parent.id,
                        "heading": render(parent),
                        "level": get_level(parent),
                    }
                    if parent else None
                ),
                "siblings": [
                    {
                        "id": h.id,
                        "heading": render(h),
                        "level": get_level(h),
                    }
                    for h in siblings
                ],
                "childrens": [
                    {
                        "id": h.id,
                        "heading": render(h),
                        "level": get_level(h),
                    }
                    for h in childrens
                ],
            }

        return None

    def apply_placeholders(self, placeholders: list[PlaceholderModel]):
        if not placeholders:
            return

        self._build_element_index()
        structural_ops: list[ElementReplacement] = []

        for ph in placeholders:

            if ph.deleted:
                element = self.get_element_by_id(ph.element_id)
                if not element:
                    continue

                if isinstance(element, ParagraphModel):
                    ParagraphModel.apply_deleted_placeholder(element, ph)

                elif isinstance(element, TableCellModel):
                    TableCellModel.apply_deleted_placeholder(element, ph)

                elif isinstance(element, (HeaderModel, FooterModel)):
                    element.apply_deleted_placeholder(ph)

                continue

            if ph.status != PlaceholderStatus.REPLACEMENT_DONE:
                continue

            element = self.get_element_by_id(ph.element_id)
            if not element:
                continue

            if isinstance(element, HeaderModel):
                element.apply_inline_placeholder(ph)
                continue

            if isinstance(element, FooterModel):
                element.apply_inline_placeholder(ph)
                continue

            if isinstance(element, ParagraphModel):
                op = resolve_structural_replacement(element, ph)
                if op is not None:
                    structural_ops.append(op)
                    continue
                ParagraphModel.apply_inline_placeholder(element, ph)

            elif isinstance(element, TableCellModel):
                TableCellModel.apply_placeholder(element, ph)

        for op in structural_ops:
            self._apply_replacement(op)

        self.invalidate_index()

    def _apply_replacement(self, op: ElementReplacement):
        for layout in self.layouts:
            for idx, elem in enumerate(layout.elements):
                if elem.id == op.old_element_id:
                    layout.elements[idx:idx + 1] = op.new_elements
                    return

    def _get_heading_path(self, elem: ParagraphModel | TableModel) -> List[str]:
        path = []
        current = elem

        while current and current.parent_ref_id:
            parent = self.get_element_by_id(current.parent_ref_id)
            if parent and isinstance(parent, ParagraphModel) and parent.is_heading:
                if parent.heading_level == 1:
                    heading_text = parent.to_text().strip()
                    if heading_text:
                        path.insert(0, heading_text)
                    break

                heading_text = parent.to_text().strip()
                if heading_text:
                    path.insert(0, heading_text)
                current = parent
            else:
                break

        return path

    def get_table_context(self, table: TableModel) -> List[str]:
        context = self._get_heading_path(table)

        if table.caption_ref_id:
            caption = self.get_element_by_id(table.caption_ref_id)
            if caption:
                text = (caption.to_text() or "").strip()
                if text:
                    context.append(text)

        lead_in = self._build_lead_in_index().get(table.id)
        if lead_in:
            context.append(lead_in)

        return context

    def _build_lead_in_index(self) -> Dict[str, str]:
        if self._lead_in_index:
            return self._lead_in_index

        recent: deque = deque(maxlen=table_context.TABLE_LEAD_IN_MAX_PARAGRAPHS)

        for elem in self.iter_elements():
            if isinstance(elem, TableModel):
                if recent:
                    self._lead_in_index[elem.id] = table_context.bounded_lead_in(recent)
                continue

            if isinstance(elem, ParagraphModel):
                text = (elem.to_text() or "").strip()
                if table_context.is_lead_in(text):
                    recent.append(text)

        return self._lead_in_index

    def _get_heading_path_for_heading(self, heading: ParagraphModel) -> List[str]:
        path = self._get_heading_path(heading)
        heading_text = heading.to_text().strip()
        if heading_text:
            path.append(heading_text)
        return path

    def build_index(self) -> FileIndexModel:
        node_map: dict[str, IndexItem] = {}
        roots: list[IndexItem] = []

        def attach(node: IndexItem, parent_id: str | None):
            if parent_id and parent_id in node_map:
                node_map[parent_id].child.append(node)
            else:
                roots.append(node)

        for elem in self.iter_elements():

            if isinstance(elem, ParagraphModel) and elem.is_heading:
                node = IndexItem(
                    id=elem.id,
                    label=elem.to_text(),
                    index=IndexType.OUTLINE,
                    child=[],
                )
                node_map[elem.id] = node
                attach(node, elem.parent_ref_id)
                continue

            if isinstance(elem, ParagraphModel) and not elem.is_caption:
                label = elem.to_text().strip()[:30]

                node = IndexItem(
                    id=elem.id,
                    label=f'{label}{"..." if len(label) == 30 else ""}',
                    index=IndexType.PARA,
                    child=[],
                )
                node_map[elem.id] = node
                attach(node, elem.parent_ref_id)
                continue

            if isinstance(elem, TableModel):
                caption = None
                if elem.caption_ref_id:
                    caption_elem = self.get_element_by_id(elem.caption_ref_id)
                    if caption_elem:
                        caption = caption_elem.to_text()

                label = (
                    caption
                    or getattr(elem, "index_number", None)
                    or f"Table {elem.id}"
                )

                node = IndexItem(
                    id=elem.id,
                    label=str(label),
                    index=IndexType.TABLE,
                    child=[],
                )
                node_map[elem.id] = node
                attach(node, elem.parent_ref_id)

        return FileIndexModel(
            id=self.id,
            filename=self.filename,
            nodes=roots,
        )
