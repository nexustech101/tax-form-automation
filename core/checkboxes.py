"""checkboxes.py

Checkbox states read from the page image.

Text is unreliable for tick marks: OCR reads a ticked box as '冈' or an empty
box as 'X', and many PDFs draw the X as vector lines with no text at all. So a
checkbox field is resolved from pixels:

1. find the first line of the field's printed label (`find_label`);
2. find the small printed squares on the page (`find_squares`);
3. pair each label with the nearest square beside it: left, right, below, or
   overlapping the start of the label line (a box printed inline). A printed
   rule between them means they sit in different cells (1098-T box 7's square
   is at the right end of its cell, next to box 8's label), so that pair is
   refused. Pairing is one-to-one, closest pairs first;
4. the box is checked when ink covers part of its interior.

Fields whose label or box is not found are left out of the result, and the
extraction model's reading stands.

*Usage:*
>>> states = checkbox_states(document.blocks, {"retirement_plan": "Retirement plan"}, document.page_image)
>>> states
{'retirement_plan': True}
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Iterable

import cv2
import numpy as np

from core.layout import Block, label_words

UNITS_PER_PT = 1000 / 612  # blocks are scaled to 0..1000 of a letter page's width
MIN_BOX_PT, MAX_BOX_PT = 5.0, 20.0
CHECKED_FILL = 0.08  # share of the box interior covered by ink
LEFT_REACH, RIGHT_REACH, BELOW_REACH = 35.0, 300.0, 60.0  # units (right: boxes at the far end of a cell)
RULE_PT = 24.0  # a straight line at least this long is a cell rule
OVERLAP = 8.0  # units a box may overlap its label's padded line box


@dataclass
class Square:
    page: int
    left: float
    top: float
    right: float
    bottom: float
    fill: float

    @property
    def checked(self) -> bool:
        return self.fill >= CHECKED_FILL


@dataclass
class LabelHit:
    page: int
    left: float
    top: float
    right: float
    bottom: float


# --- Labels --------------------------------------------------------------------


def find_label(blocks: Iterable[Block], label: str) -> LabelHit | None:
    """
    The block where `label` starts. Long labels wrap, so the block only needs
    to hold the beginning of the label; several labels on one PDF line are
    located by character position.
    """
    target = label_words(label)
    best, best_score = None, 0.0
    for b in blocks:
        text = label_words(b.text)
        if len(text) < 4:
            continue
        if target.startswith(text) and len(text) >= min(len(target), 12):
            score = 1.0 + len(text) / len(target)
            hit = LabelHit(b.page, b.left, b.top, b.right, b.bottom)
        elif text.startswith(target):
            score = 1.0 + len(target) / len(text)
            hit = LabelHit(b.page, b.left, b.top, b.right, b.bottom)
        elif target in text:  # joined onto other text on the line: estimate the label's span
            start = text.index(target) / len(text)
            end = (text.index(target) + len(target)) / len(text)
            width = b.right - b.left
            score = 1.0 + len(target) / len(text)
            hit = LabelHit(b.page, b.left + start * width, b.top, b.left + end * width, b.bottom)
        else:
            head = target[: len(text)]
            ratio = SequenceMatcher(None, text, head).ratio() if len(text) >= 8 else 0.0
            if ratio < 0.85:
                continue
            score = ratio
            hit = LabelHit(b.page, b.left, b.top, b.right, b.bottom)
        if score > best_score:
            best, best_score = hit, score
    return best


# --- Squares -------------------------------------------------------------------


@dataclass
class PageGeometry:
    squares: list[Square]
    vertical: np.ndarray  # mask of vertical rules
    horizontal: np.ndarray  # mask of horizontal rules
    px_per_unit: float

    def ruled_between_x(self, x0: float, x1: float, y: float) -> bool:
        """A vertical rule crosses the row `y` between x0 and x1 (units)."""
        k = self.px_per_unit
        lo, hi = sorted((int(x0 * k) + 1, int(x1 * k) - 1))
        row = int(y * k)
        return hi > lo and 0 <= row < self.vertical.shape[0] and bool(self.vertical[row, lo:hi].any())

    def ruled_between_y(self, y0: float, y1: float, x: float) -> bool:
        """A horizontal rule crosses the column `x` between y0 and y1 (units)."""
        k = self.px_per_unit
        lo, hi = sorted((int(y0 * k) + 1, int(y1 * k) - 1))
        col = int(x * k)
        return hi > lo and 0 <= col < self.horizontal.shape[1] and bool(self.horizontal[lo:hi, col].any())


def page_geometry(gray: np.ndarray, page: int) -> PageGeometry:
    """
    Printed checkbox squares and cell rules on a grayscale page image, in block
    units. A square is a dark component 5-20 pt on a side, roughly square,
    with ink along all four edges and in all four corners (letters like O, D,
    0 fail). A rule is a straight run of ink at least `RULE_PT` long.
    """
    px_per_unit = gray.shape[1] / 1000
    _, dark = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rule_px = max(3, int(RULE_PT * UNITS_PER_PT * px_per_unit))
    # Thicken across the line first so a slightly skewed scan still has an unbroken run.
    vertical = cv2.morphologyEx(cv2.dilate(dark, np.ones((1, 3), np.uint8)), cv2.MORPH_OPEN,
                                np.ones((rule_px, 1), np.uint8))
    horizontal = cv2.morphologyEx(cv2.dilate(dark, np.ones((3, 1), np.uint8)), cv2.MORPH_OPEN,
                                  np.ones((1, rule_px), np.uint8))
    return PageGeometry(find_squares(gray, page, dark), vertical, horizontal, px_per_unit)


def find_squares(gray: np.ndarray, page: int, dark: np.ndarray | None = None) -> list[Square]:
    """Printed checkbox squares on a grayscale page image (see `page_geometry`)."""
    px_per_unit = gray.shape[1] / 1000
    if dark is None:
        _, dark = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    lo = MIN_BOX_PT * UNITS_PER_PT * px_per_unit
    hi = MAX_BOX_PT * UNITS_PER_PT * px_per_unit
    squares: list[Square] = []
    for i in range(1, count):
        x, y, w, h, _area = stats[i]
        if not (lo <= w <= hi and lo <= h <= hi and 0.75 <= w / h <= 1.33):
            continue
        component = labels[y:y + h, x:x + w] == i
        edge = max(2, round(min(w, h) * 0.15))
        sides = (
            component[:edge].any(axis=0).mean(), component[-edge:].any(axis=0).mean(),
            component[:, :edge].any(axis=1).mean(), component[:, -edge:].any(axis=1).mean(),
        )
        corners = (component[:edge, :edge], component[:edge, -edge:], component[-edge:, :edge], component[-edge:, -edge:])
        if min(sides) < 0.8 or not all(c.any() for c in corners):
            continue
        inset = max(edge + 1, round(min(w, h) * 0.22))
        interior = dark[y + inset:y + h - inset, x + inset:x + w - inset]
        fill = float(interior.mean()) if interior.size else 0.0
        squares.append(Square(page, float(x / px_per_unit), float(y / px_per_unit), float((x + w) / px_per_unit),
                              float((y + h) / px_per_unit), fill))
    return squares


def _distance(label: LabelHit, sq: Square, geometry: PageGeometry | None = None) -> float | None:
    """How well `sq` sits as the box of `label`; None when it is not beside it (or a rule separates them)."""
    if sq.page != label.page:
        return None
    line_h = max(label.bottom - label.top, 6.0)
    cy = (sq.top + sq.bottom) / 2
    cx = (sq.left + sq.right) / 2
    same_line = label.top - line_h * 0.6 <= cy <= label.bottom + line_h * 0.6
    if same_line and label.left - OVERLAP <= sq.left <= label.left + 30 and sq.right <= label.left + 40:
        # At the start of the label line: the text layer ran the mark into the label. The mark may
        # belong to the previous cell's box, in which case the cell rule runs between them.
        if geometry and geometry.ruled_between_x(sq.left, label.left + 40, cy):
            return None
        return 0.0
    # OCR line boxes are padded a few units, so a box can overlap the label's edge.
    if same_line and sq.left < label.left and -OVERLAP <= label.left - sq.right <= LEFT_REACH:
        if geometry and geometry.ruled_between_x(sq.right, label.left, cy):
            return None
        return max(0.0, label.left - sq.right)
    if same_line and sq.right > label.right and -OVERLAP <= sq.left - label.right <= RIGHT_REACH:
        if geometry and geometry.ruled_between_x(label.right, sq.left, cy):
            return None
        return max(0.0, sq.left - label.right) + 5
    if 0 <= sq.top - label.bottom <= BELOW_REACH and label.left - 15 <= cx <= label.right + 15:
        if geometry and geometry.ruled_between_y(label.bottom, sq.top, cx):
            return None
        return sq.top - label.bottom + 8
    return None


def checkbox_states(
    blocks: list[Block],
    labels: dict[str, str],
    page_image: Callable[[int], np.ndarray | None],
) -> dict[str, bool]:
    """
    Field name -> checked, for every checkbox whose label and box were found.
    `labels` maps field name -> printed label; `page_image(page_no)` returns a
    grayscale page whose width spans the same 0..1000 units as the blocks.
    """
    hits = {name: hit for name, label in labels.items() if (hit := find_label(blocks, label))}
    pages: dict[int, PageGeometry] = {}
    for page in sorted({h.page for h in hits.values()}):
        image = page_image(page)
        if image is not None:
            pages[page] = page_geometry(image, page)
    squares = [sq for geometry in pages.values() for sq in geometry.squares]

    pairs = sorted(
        (d, name, i)
        for name, hit in hits.items()
        for i, sq in enumerate(squares)
        if (d := _distance(hit, sq, pages.get(sq.page))) is not None
    )
    states: dict[str, bool] = {}
    used: set[int] = set()
    for _d, name, i in pairs:
        if name in states or i in used:
            continue
        states[name] = squares[i].checked
        used.add(i)
    return states
