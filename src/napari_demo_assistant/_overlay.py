from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from qtpy.QtCore import QPoint, Qt, QTimer, Signal
from qtpy.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from qtpy.QtWidgets import QWidget


@dataclass
class ArrowAnnotation:
    start: tuple[int, int]
    end: tuple[int, int]
    text: str = ""


@dataclass
class TextAnnotation:
    pos: tuple[int, int]
    text: str


@dataclass
class NumberedCircleAnnotation:
    center: tuple[int, int]
    number: int
    radius: int = 34
    text: str = ""


@dataclass
class ClickRipple:
    center: tuple[int, int]
    started_at: float
    duration_sec: float = 0.55
    max_radius: int = 46


@dataclass(frozen=True)
class AnnotationPalette:
    name: str
    arrow: QColor
    circle: QColor
    text: QColor
    background: QColor
    outline: QColor


PALETTES: dict[str, AnnotationPalette] = {
    "Orange / Yellow": AnnotationPalette(
        name="Orange / Yellow",
        arrow=QColor(255, 80, 40),
        circle=QColor(255, 220, 40),
        text=QColor(255, 255, 255),
        background=QColor(0, 0, 0, 200),
        outline=QColor(255, 255, 255),
    ),
    "Cyan / Magenta": AnnotationPalette(
        name="Cyan / Magenta",
        arrow=QColor(0, 255, 255),
        circle=QColor(255, 0, 255),
        text=QColor(255, 255, 255),
        background=QColor(0, 0, 0, 210),
        outline=QColor(255, 255, 255),
    ),
    "Lime / Amber": AnnotationPalette(
        name="Lime / Amber",
        arrow=QColor(0, 255, 80),
        circle=QColor(255, 170, 0),
        text=QColor(255, 255, 255),
        background=QColor(0, 0, 0, 210),
        outline=QColor(255, 255, 255),
    ),
    "White / Black": AnnotationPalette(
        name="White / Black",
        arrow=QColor(255, 255, 255),
        circle=QColor(255, 255, 255),
        text=QColor(255, 255, 255),
        background=QColor(0, 0, 0, 230),
        outline=QColor(255, 255, 255),
    ),
    "Black / Yellow": AnnotationPalette(
        name="Black / Yellow",
        arrow=QColor(0, 0, 0),
        circle=QColor(255, 235, 0),
        text=QColor(0, 0, 0),
        background=QColor(255, 235, 0, 230),
        outline=QColor(0, 0, 0),
    ),
}


class AnnotationOverlay(QWidget):
    """
    Transparent annotation overlay for napari demo recording.

    Modes:
    - off: annotations stay visible, but the overlay is mouse-transparent so
      napari and plugin buttons receive mouse input normally.
    - arrow: drag from arrow tail to arrow head. Optional narrative text is drawn at the tail.
    - text: click to place optional narrative text.
    - numbered_circle: click to place a numbered high-contrast circle.

    Mouse shortcut:
    - right click: exits drawing mode but keeps existing annotations visible.
    """

    annotation_changed = Signal()
    drawing_exited = Signal()
    number_used = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.mode = "off"
        self.narrative_text = ""
        self.show_narrative = False
        self.current_number = 1
        self.palette_name = "Orange / Yellow"
        self.palette = PALETTES[self.palette_name]

        self.arrows: list[ArrowAnnotation] = []
        self.texts: list[TextAnnotation] = []
        self.numbered_circles: list[NumberedCircleAnnotation] = []
        self.click_ripples: list[ClickRipple] = []

        # Append-only drawing order for simple undo/redo.
        # Each item is (kind, annotation_object). The per-kind lists are kept
        # because paintEvent draws from those lists.
        self._annotation_order: list[tuple[str, object]] = []
        self._redo_stack: list[tuple[str, object]] = []

        self._drag_start: Optional[QPoint] = None
        self._drag_current: Optional[QPoint] = None

        self._ripple_timer = QTimer(self)
        self._ripple_timer.setInterval(16)
        self._ripple_timer.timeout.connect(self._on_ripple_timer)

        # Start inactive and invisible. set_mode("off") is called by the widget
        # after the overlay geometry/palette are configured.
        self.hide()

    def set_mode(self, mode: str):
        self.mode = mode

        if mode == "off":
            # Keep existing annotations visible, but stop hijacking mouse clicks.
            # This is the key difference from hiding the overlay.
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.clearFocus()
            self._drag_start = None
            self._drag_current = None
            self.show()
            self.raise_()
            self.update()
            self.drawing_exited.emit()
            return

        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.show()
        self.raise_()
        self.setFocus()
        self.update()

    def add_click_ripple_at_global(self, global_pos: QPoint):
        """Add a short visual click pulse at a global screen position."""
        local_pos = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local_pos):
            return

        self.click_ripples.append(
            ClickRipple(center=(local_pos.x(), local_pos.y()), started_at=time.monotonic())
        )
        if not self._ripple_timer.isActive():
            self._ripple_timer.start()
        self.show()
        self.raise_()
        self.update()

    def set_narrative_text(self, text: str, enabled: bool):
        self.narrative_text = text.strip()
        self.show_narrative = bool(enabled and self.narrative_text)
        self.update()

    def set_current_number(self, number: int):
        self.current_number = max(1, int(number))
        self.update()

    def set_palette(self, palette_name: str):
        self.palette_name = palette_name if palette_name in PALETTES else "Orange / Yellow"
        self.palette = PALETTES[self.palette_name]
        self.update()

    def clear_annotations(self):
        self.arrows.clear()
        self.texts.clear()
        self.numbered_circles.clear()
        self._annotation_order.clear()
        self._redo_stack.clear()
        self._drag_start = None
        self._drag_current = None
        self.update()
        self.annotation_changed.emit()

    def can_undo(self) -> bool:
        return bool(self._annotation_order)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo_last_annotation(self) -> bool:
        if not self._annotation_order:
            return False

        kind, annotation = self._annotation_order.pop()
        self._remove_annotation_from_kind_list(kind, annotation)
        self._redo_stack.append((kind, annotation))
        self.update()
        self.annotation_changed.emit()
        return True

    def redo_last_annotation(self) -> bool:
        if not self._redo_stack:
            return False

        kind, annotation = self._redo_stack.pop()
        self._append_annotation(kind, annotation, clear_redo=False)
        self.update()
        self.annotation_changed.emit()
        return True

    def _append_annotation(self, kind: str, annotation: object, *, clear_redo: bool = True):
        if kind == "arrow":
            self.arrows.append(annotation)
        elif kind == "text":
            self.texts.append(annotation)
        elif kind == "numbered_circle":
            self.numbered_circles.append(annotation)
        else:
            raise ValueError(f"Unknown annotation kind: {kind}")

        self._annotation_order.append((kind, annotation))
        if clear_redo:
            self._redo_stack.clear()

    def _remove_annotation_from_kind_list(self, kind: str, annotation: object):
        target_list: list
        if kind == "arrow":
            target_list = self.arrows
        elif kind == "text":
            target_list = self.texts
        elif kind == "numbered_circle":
            target_list = self.numbered_circles
        else:
            return

        for index in range(len(target_list) - 1, -1, -1):
            if target_list[index] is annotation:
                target_list.pop(index)
                return

    def _on_ripple_timer(self):
        now = time.monotonic()
        self.click_ripples = [
            ripple for ripple in self.click_ripples
            if now - ripple.started_at <= ripple.duration_sec
        ]
        if not self.click_ripples:
            self._ripple_timer.stop()
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key_Escape:
            self.set_mode("off")
            event.accept()
            return

        if key == Qt.Key_Z and modifiers & Qt.ControlModifier:
            if modifiers & Qt.ShiftModifier:
                self.redo_last_annotation()
            else:
                self.undo_last_annotation()
            event.accept()
            return

        if key == Qt.Key_Y and modifiers & Qt.ControlModifier:
            self.redo_last_annotation()
            event.accept()
            return

        # Del/Backspace intentionally clear all annotations. This remains a
        # power-user shortcut; the safer normal correction path is Ctrl+Z.
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.clear_annotations()
            event.accept()
            return

        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        # Fast mouse-only escape path. Existing annotations remain visible.
        if event.button() == Qt.RightButton:
            self.set_mode("off")
            event.accept()
            return

        if self.mode == "arrow":
            self._drag_start = event.pos()
            self._drag_current = event.pos()
            event.accept()
            return

        if self.mode == "text":
            if self.show_narrative:
                annotation = TextAnnotation(
                    pos=(event.pos().x(), event.pos().y()),
                    text=self.narrative_text,
                )
                self._append_annotation("text", annotation)
                self.update()
                self.annotation_changed.emit()
            event.accept()
            return

        if self.mode == "numbered_circle":
            annotation = NumberedCircleAnnotation(
                center=(event.pos().x(), event.pos().y()),
                number=self.current_number,
                radius=34,
                text=self.narrative_text if self.show_narrative else "",
            )
            self._append_annotation("numbered_circle", annotation)
            self.update()
            self.annotation_changed.emit()
            self.number_used.emit(self.current_number)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.mode == "arrow" and self._drag_start is not None:
            self._drag_current = event.pos()
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.mode == "arrow" and self._drag_start is not None:
            start = self._drag_start
            end = event.pos()
            annotation = ArrowAnnotation(
                start=(start.x(), start.y()),
                end=(end.x(), end.y()),
                text=self.narrative_text if self.show_narrative else "",
            )
            self._append_annotation("arrow", annotation)
            self._drag_start = None
            self._drag_current = None
            self.update()
            self.annotation_changed.emit()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        painter.setFont(font)

        arrow_pen = QPen(self.palette.arrow, 5)
        circle_pen = QPen(self.palette.circle, 5)

        for arrow in self.arrows:
            self._draw_arrow(painter, QPoint(*arrow.start), QPoint(*arrow.end), arrow_pen)
            if arrow.text:
                self._draw_text_box(painter, QPoint(arrow.start[0] + 12, arrow.start[1] - 12), arrow.text)

        if self._drag_start is not None and self._drag_current is not None:
            self._draw_arrow(painter, self._drag_start, self._drag_current, arrow_pen)
            if self.show_narrative:
                self._draw_text_box(
                    painter,
                    QPoint(self._drag_start.x() + 12, self._drag_start.y() - 12),
                    self.narrative_text,
                )

        for text in self.texts:
            self._draw_text_box(painter, QPoint(*text.pos), text.text)

        for circle in self.numbered_circles:
            self._draw_numbered_circle(painter, circle, circle_pen)

        self._draw_click_ripples(painter)

    def _draw_click_ripples(self, painter: QPainter):
        if not self.click_ripples:
            return

        now = time.monotonic()
        for ripple in self.click_ripples:
            age = now - ripple.started_at
            progress = min(max(age / ripple.duration_sec, 0.0), 1.0)
            radius = int(10 + progress * ripple.max_radius)
            alpha = int(220 * (1.0 - progress))
            if alpha <= 0:
                continue

            color = QColor(self.palette.circle)
            color.setAlpha(alpha)
            pen = QPen(color, 4)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPoint(*ripple.center), radius, radius)

            inner = QColor(self.palette.arrow)
            inner.setAlpha(max(40, alpha // 2))
            painter.setPen(QPen(inner, 2))
            painter.drawEllipse(QPoint(*ripple.center), max(3, radius // 3), max(3, radius // 3))

    def _draw_numbered_circle(self, painter: QPainter, circle: NumberedCircleAnnotation, pen: QPen):
        center = QPoint(*circle.center)
        painter.setPen(pen)
        painter.setBrush(self.palette.background)
        painter.drawEllipse(center, circle.radius, circle.radius)

        number_text = str(circle.number)
        number_font = QFont(painter.font())
        number_font.setPointSize(22)
        number_font.setBold(True)
        painter.setFont(number_font)
        painter.setPen(QPen(self.palette.text, 2))

        metrics = QFontMetrics(number_font)
        x = center.x() - metrics.horizontalAdvance(number_text) // 2
        y = center.y() + metrics.ascent() // 2 - 3
        painter.drawText(QPoint(x, y), number_text)

        if circle.text:
            label_anchor = QPoint(center.x() + circle.radius + 10, center.y() - 4)
            self._draw_text_box(painter, label_anchor, circle.text)

    def _draw_text_box(self, painter: QPainter, anchor: QPoint, text: str):
        if not text:
            return

        label_font = QFont(painter.font())
        label_font.setPointSize(16)
        label_font.setBold(True)
        painter.setFont(label_font)

        metrics = QFontMetrics(label_font)
        text_width = metrics.horizontalAdvance(text)
        text_height = metrics.height()

        padding_x = 10
        padding_y = 7
        x = anchor.x()
        y = anchor.y() - text_height

        box_width = min(max(text_width + padding_x * 2, 80), max(80, self.width() - 20))
        box_height = text_height + padding_y * 2

        if x + box_width > self.width() - 6:
            x = max(6, self.width() - box_width - 6)
        if y < 6:
            y = 6

        painter.fillRect(x, y, box_width, box_height, self.palette.background)
        painter.setPen(QPen(self.palette.outline, 2))
        painter.drawRect(x, y, box_width, box_height)
        painter.setPen(QPen(self.palette.text, 2))
        painter.drawText(QPoint(x + padding_x, y + padding_y + metrics.ascent()), text)

    def _draw_arrow(self, painter: QPainter, start: QPoint, end: QPoint, pen: QPen):
        painter.setPen(pen)
        painter.drawLine(start, end)

        dx = end.x() - start.x()
        dy = end.y() - start.y()
        length = max((dx * dx + dy * dy) ** 0.5, 1)

        ux = dx / length
        uy = dy / length

        arrow_size = 22
        left = QPoint(
            int(end.x() - arrow_size * ux - arrow_size * uy * 0.5),
            int(end.y() - arrow_size * uy + arrow_size * ux * 0.5),
        )
        right = QPoint(
            int(end.x() - arrow_size * ux + arrow_size * uy * 0.5),
            int(end.y() - arrow_size * uy - arrow_size * ux * 0.5),
        )

        painter.drawLine(end, left)
        painter.drawLine(end, right)
