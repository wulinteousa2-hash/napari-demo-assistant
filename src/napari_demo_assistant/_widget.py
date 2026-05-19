from __future__ import annotations

import json
import re
import time
from importlib import metadata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import mss
import mss.tools
from qtpy.QtCore import QEvent, QPoint, QSettings, QTimer, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QApplication,
    QDoubleSpinBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ._overlay import PALETTES, AnnotationOverlay
from ._recorder import ScreenRecorderWorker


BUG_REPORT_URL = "https://github.com/wulinteousa2-hash/napari-demo-assistant/issues"
PACKAGE_NAME = "napari-demo-assistant"
CAPTION_DURATION_SEC = 3.0


class _CollapsibleSection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(4)

        header = QWidget()
        header.setObjectName("SectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self._toggle = QToolButton()
        self._toggle.setObjectName("SectionToggle")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setText("-")
        self._toggle.setFixedWidth(24)
        self._toggle.setToolTip(f"Collapse or expand {title}")

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")

        header_layout.addWidget(self._toggle)
        header_layout.addWidget(title_label)
        header_layout.addStretch(1)

        self.body = QGroupBox()
        self.body.setObjectName("CollapsibleBody")
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        root_layout.addWidget(header)
        root_layout.addWidget(self.body)

        self._toggle.toggled.connect(self._set_expanded)

    def set_content_layout(self, layout):
        self.body.setLayout(layout)

    def _set_expanded(self, expanded: bool):
        self.body.setVisible(expanded)
        self._toggle.setText("-" if expanded else "+")


def _allow_horizontal_shrink(widget: QWidget):
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Preferred, widget.sizePolicy().verticalPolicy())


@dataclass
class StepMarker:
    time_sec: float
    text: str
    frame_index: int
    timestamp: str


@dataclass
class ActionMarker:
    time_sec: float
    frame_index: int
    timestamp: str
    widget_type: str
    text: str
    tooltip: str
    object_name: str
    parent_type: str
    action: str


class DemoAssistantWidget(QWidget):
    def __init__(self, napari_viewer):
        super().__init__()
        self.setWindowTitle("Demo Assistant")
        self.viewer = napari_viewer

        self.worker: Optional[ScreenRecorderWorker] = None
        self.steps: list[StepMarker] = []
        self.actions: list[ActionMarker] = []
        self.current_step_text = ""
        self.output_path: Optional[Path] = None

        self.overlay: Optional[AnnotationOverlay] = None
        self.overlay_target_widget: Optional[QWidget] = None
        self._click_event_filter_installed = False
        self._recording_paused = False
        self._last_logged_action_key = None
        self._last_logged_action_time = 0.0
        self._detached_drag_widgets: set[QWidget] = set()
        self._detached_drag_target: Optional[QWidget] = None
        self._detached_drag_offset: Optional[QPoint] = None

        self.settings = QSettings("napari-demo-assistant", "napari-demo-assistant")

        self._build_ui()
        self._apply_styles()
        self._connect_signals()
        self._load_settings()
        self._set_idle_state()
        QTimer.singleShot(0, self._configure_parent_dock)
        self.destroyed.connect(self._on_destroyed)

    def eventFilter(self, obj, event):
        """Draw click ripples and record UI actions without blocking Qt events."""
        if self._handle_detached_drag_event(obj, event):
            return True

        if (
            event.type() == QEvent.MouseButtonPress
            and hasattr(event, "button")
            and event.button() == Qt.LeftButton
        ):
            self._maybe_add_click_ripple(event)
            self._maybe_log_action(obj)

        return super().eventFilter(obj, event)

    def _register_detached_drag_widget(self, widget: QWidget):
        self._detached_drag_widgets.add(widget)
        widget.installEventFilter(self)
        widget.setCursor(Qt.OpenHandCursor)
        widget.setToolTip("Drag here to move the detached Demo Assistant window.")

    def _configure_parent_dock(self):
        dock = self._find_parent_dock()
        if dock is None:
            return

        if hasattr(dock, "setWindowTitle"):
            dock.setWindowTitle("Demo Assistant")

        if hasattr(dock, "setFeatures") and hasattr(QDockWidget, "DockWidgetMovable"):
            dock.setFeatures(
                QDockWidget.DockWidgetClosable
                | QDockWidget.DockWidgetMovable
                | QDockWidget.DockWidgetFloatable
            )

    def _find_parent_dock(self) -> Optional[QDockWidget]:
        current = self.parent()
        while current is not None:
            if isinstance(current, QDockWidget):
                return current
            current = current.parent() if hasattr(current, "parent") else None
        return None

    def _event_global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _floating_drag_target(self) -> Optional[QWidget]:
        dock = self._find_parent_dock()
        if dock is not None and dock.isFloating():
            return dock

        return None

    def _handle_detached_drag_event(self, obj, event) -> bool:
        if obj not in self._detached_drag_widgets:
            return False

        event_type = event.type()
        if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            target = self._floating_drag_target()
            if target is None:
                return False
            self._detached_drag_target = target
            self._detached_drag_offset = self._event_global_pos(event) - target.pos()
            target.raise_()
            obj.setCursor(Qt.ClosedHandCursor)
            return True

        if event_type == QEvent.MouseMove and self._detached_drag_target is not None:
            if event.buttons() & Qt.LeftButton and self._detached_drag_offset is not None:
                self._detached_drag_target.move(
                    self._event_global_pos(event) - self._detached_drag_offset
                )
                return True

        if event_type == QEvent.MouseButtonRelease:
            if self._detached_drag_target is not None:
                self._detached_drag_target = None
                self._detached_drag_offset = None
                obj.setCursor(Qt.OpenHandCursor)
                return True

        return False

    def hideEvent(self, event):
        self._cleanup_annotation_overlay(
            update_ui=True,
            log_message="Annotation overlay removed because the widget was hidden.",
        )
        super().hideEvent(event)

    def _install_click_event_filter(self):
        if self._click_event_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._click_event_filter_installed = True

    def _remove_click_event_filter(self):
        if not self._click_event_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._click_event_filter_installed = False

    def _refresh_event_filter(self):
        action_logging_needed = (
            hasattr(self, "action_log_check")
            and self.action_log_check.isChecked()
            and self._is_recording_active()
        )
        if self.overlay is not None or action_logging_needed:
            self._install_click_event_filter()
        else:
            self._remove_click_event_filter()

    def _cleanup_annotation_overlay(self, update_ui: bool = True, log_message: Optional[str] = None):
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
            self.overlay_target_widget = None

        self._remove_click_event_filter()

        if update_ui:
            self._update_undo_redo_buttons()
            self._set_status_chips("off", "hidden", "ready")
            self.status_label.setText("Status: Annotation overlay removed")
            if log_message:
                self._log(log_message)

    def _on_destroyed(self, _obj=None):
        self._cleanup_annotation_overlay(update_ui=False)

    def _sync_overlay_geometry(self):
        if self.overlay is not None and self.overlay_target_widget is not None:
            self.overlay.setGeometry(self.overlay_target_widget.rect())
            self.overlay.raise_()

    def _is_recording_active(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _maybe_add_click_ripple(self, event):
        if self.overlay is None or self.overlay_target_widget is None:
            return

        try:
            if hasattr(event, "globalPosition"):
                global_pos = event.globalPosition().toPoint()
            else:
                global_pos = event.globalPos()

            self._sync_overlay_geometry()
            self.overlay.add_click_ripple_at_global(global_pos)
        except Exception:
            # Never let the visual demo aid interfere with napari/user events.
            pass

    def _maybe_log_action(self, obj):
        if not self.action_log_check.isChecked() or not self._is_recording_active():
            return

        try:
            if self._is_overlay_event_object(obj):
                return

            widget = self._find_loggable_widget(obj)
            if widget is None:
                return

            self._record_action_from_widget(widget)
        except Exception as exc:
            self._log(f"Action log skipped: {exc}")

    def _is_overlay_event_object(self, obj) -> bool:
        if self.overlay is None:
            return False

        current = obj
        while current is not None:
            if current is self.overlay:
                return True
            current = current.parent() if hasattr(current, "parent") else None
        return False

    def _find_loggable_widget(self, obj):
        loggable_types = (
            QPushButton,
            QToolButton,
            QCheckBox,
            QRadioButton,
            QComboBox,
            QSpinBox,
            QDoubleSpinBox,
            QLineEdit,
        )

        current = obj
        while current is not None:
            if isinstance(current, loggable_types):
                return current
            current = current.parent() if hasattr(current, "parent") else None
        return None

    def _describe_widget_action(self, widget) -> dict:
        widget_type = widget.__class__.__name__
        tooltip = widget.toolTip() if hasattr(widget, "toolTip") else ""
        object_name = widget.objectName() if hasattr(widget, "objectName") else ""
        parent = widget.parent() if hasattr(widget, "parent") else None
        parent_type = parent.__class__.__name__ if parent is not None else ""

        action = "click"
        text = ""

        if isinstance(widget, QComboBox):
            action = "combo_open"
            text = widget.currentText() or object_name or "Combo box"
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            action = "spin_focus"
            if widget is self.fps_spin:
                text = f"FPS = {widget.value()}"
            elif widget is self.crf_spin:
                text = f"CRF = {widget.value()}"
            elif object_name:
                text = f"{object_name} = {widget.value()}"
            else:
                text = f"Value = {widget.value()}"
        elif isinstance(widget, QLineEdit):
            action = "click"
            label = widget.placeholderText() or object_name
            text = f"Focused text field: {label}" if label else "Focused text field"
        elif isinstance(widget, (QCheckBox, QRadioButton)):
            action = "toggle"
            text = widget.text() or object_name or widget_type
        elif hasattr(widget, "text"):
            text = widget.text() or object_name or widget_type
        else:
            text = object_name or widget_type

        return {
            "widget_type": widget_type,
            "text": text,
            "tooltip": tooltip,
            "object_name": object_name,
            "parent_type": parent_type,
            "action": action,
        }

    def _record_action_from_widget(self, widget):
        info = self._describe_widget_action(widget)
        action_key = (
            info["widget_type"],
            info["text"],
            info["object_name"],
        )
        now = time.monotonic()
        if (
            self._last_logged_action_key == action_key
            and now - self._last_logged_action_time < 0.25
        ):
            return

        self._last_logged_action_key = action_key
        self._last_logged_action_time = now

        elapsed = 0.0
        frame_index = 0
        if self.worker is not None:
            elapsed = round(self.worker.elapsed_sec, 3)
            frame_index = int(self.worker.frame_index)

        action = ActionMarker(
            time_sec=elapsed,
            frame_index=frame_index,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            widget_type=info["widget_type"],
            text=info["text"],
            tooltip=info["tooltip"],
            object_name=info["object_name"],
            parent_type=info["parent_type"],
            action=info["action"],
        )
        self.actions.append(action)
        self._log(f'Action logged: {action.widget_type} "{action.text}"')

    def _build_ui(self):
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("MainScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMinimumHeight(0)
        scroll_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        content = QWidget()
        content.setObjectName("ScrollContent")
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        title_block = QWidget()
        title_layout = QVBoxLayout(title_block)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(1)

        title = QLabel("napari-demo-assistant")
        title.setObjectName("TitleLabel")
        title.setWordWrap(True)
        subtitle = QLabel(
            "Record napari demos with arrows, labels, and numbered steps."
        )
        subtitle.setObjectName("SubtitleLabel")
        subtitle.setWordWrap(True)
        for shrink_widget in (title_block, title, subtitle):
            _allow_horizontal_shrink(shrink_widget)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        header_layout.addWidget(title_block, stretch=1)
        for drag_widget in (header, title_block, title, subtitle):
            self._register_detached_drag_widget(drag_widget)

        self.about_btn = QPushButton("⋮")
        self.about_btn.setObjectName("HeaderIconButton")
        self.about_btn.setToolTip("About napari-demo-assistant")
        header_layout.addWidget(self.about_btn)

        layout.addWidget(header)

        # ------------------------------------------------------------------
        # Recording
        # ------------------------------------------------------------------
        settings_box = _CollapsibleSection("1  Recording")
        settings_layout = QFormLayout()
        settings_layout.setContentsMargins(12, 12, 12, 10)
        settings_layout.setSpacing(8)
        settings_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        settings_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        settings_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.target_combo = QComboBox()
        self.target_combo.addItems(["Full napari window", "Viewer canvas only"])

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(12)

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(18, 35)
        self.crf_spin.setValue(28)
        crf_tooltip = (
            "Video quality / file size. Lower CRF looks better but creates larger files; "
            "higher CRF creates smaller files. Example: 23 = higher quality, 28 = compact default, 32 = smaller file."
        )
        self.crf_spin.setToolTip(crf_tooltip)
        crf_label = QLabel("▫  CRF quality")
        crf_label.setToolTip(crf_tooltip)

        self.video_text_overlay_check = QCheckBox("Elapsed time / step text on video")
        self.video_text_overlay_check.setChecked(True)

        self.action_log_check = QCheckBox("Log clicked controls")
        self.action_log_check.setChecked(True)
        self.action_log_check.setToolTip(
            "Save button/control clicks beside the video as an action log."
        )

        self.srt_export_check = QCheckBox("Export YouTube SRT captions")
        self.srt_export_check.setChecked(True)
        self.srt_export_check.setToolTip(
            "Save a plain UTF-8 .srt caption file beside the video for YouTube upload."
        )

        self.srt_actions_check = QCheckBox("Include action clicks in captions")
        self.srt_actions_check.setChecked(False)
        self.srt_actions_check.setToolTip(
            "Add logged button/control clicks to the SRT captions. Leave off for cleaner tutorial subtitles."
        )

        self.output_line = QLineEdit()
        self.output_line.setPlaceholderText("Choose output .mp4 path")
        self.choose_output_btn = QPushButton("Browse")
        self.keep_output_check = QCheckBox("Remember output path")
        self.keep_output_check.setChecked(True)

        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_layout.addWidget(self.output_line, stretch=1)
        output_layout.addWidget(self.choose_output_btn)

        settings_layout.addRow("⌖  Target", self.target_combo)
        settings_layout.addRow("◴  FPS", self.fps_spin)
        settings_layout.addRow(crf_label, self.crf_spin)
        settings_layout.addRow("▣  Video overlay", self.video_text_overlay_check)
        settings_layout.addRow("☰  Action log", self.action_log_check)
        settings_layout.addRow("CC  Captions", self.srt_export_check)
        settings_layout.addRow("", self.srt_actions_check)
        settings_layout.addRow("▰  Output", output_row)
        settings_layout.addRow("", self.keep_output_check)
        settings_box.set_content_layout(settings_layout)
        layout.addWidget(settings_box)

        # ------------------------------------------------------------------
        # Recording controls
        # ------------------------------------------------------------------
        controls_box = _CollapsibleSection("2  Record Controls")
        controls_layout = QVBoxLayout()
        controls_layout.setContentsMargins(12, 12, 12, 10)
        controls_layout.setSpacing(8)
        controls_buttons_layout = QHBoxLayout()
        controls_buttons_layout.setContentsMargins(0, 0, 0, 0)
        controls_buttons_layout.setSpacing(8)
        self.start_btn = QPushButton("● Start")
        self.pause_btn = QPushButton("Ⅱ Pause")
        self.stop_btn = QPushButton("■ Stop")
        self.start_btn.setObjectName("StartButton")
        self.pause_btn.setObjectName("PauseButton")
        self.stop_btn.setObjectName("StopButton")
        controls_buttons_layout.addWidget(self.start_btn)
        controls_buttons_layout.addWidget(self.pause_btn)
        controls_buttons_layout.addWidget(self.stop_btn)
        controls_layout.addLayout(controls_buttons_layout)
        self.snapshot_btn = QPushButton("▣ Snapshot PNG")
        self.snapshot_btn.setObjectName("SnapshotButton")
        self.snapshot_btn.setToolTip(
            "Save a PNG image of the selected capture target, including visible annotations."
        )
        controls_layout.addWidget(self.snapshot_btn)
        self.frame_label = QLabel("▣  Frames: 0   •   ◷  Time: 0.0 s")
        self.frame_label.setObjectName("ActivityLine")
        controls_layout.addWidget(self.frame_label)
        controls_box.set_content_layout(controls_layout)
        layout.addWidget(controls_box)

        # ------------------------------------------------------------------
        # Steps / narrative
        # ------------------------------------------------------------------
        step_box = _CollapsibleSection("3  Step / Narrative")
        step_layout = QFormLayout()
        step_layout.setContentsMargins(12, 12, 12, 10)
        step_layout.setSpacing(8)
        step_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        step_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.step_number_spin = QSpinBox()
        self.step_number_spin.setRange(1, 999)
        self.step_number_spin.setValue(1)

        self.auto_increment_check = QCheckBox("Auto-increment")
        self.auto_increment_check.setChecked(True)

        self.narrative_check = QCheckBox("Attach narrative to arrow / text / circle")
        self.narrative_check.setChecked(False)
        self.narrative_check.setToolTip(
            "Use this text as a label when placing arrows, text stamps, or step circles."
        )

        self.step_input = QLineEdit()
        self.step_input.setPlaceholderText("Optional timeline/video label")
        self.step_input.setToolTip(
            "Text for the recorded video overlay, timeline marker, and optional annotation narrative."
        )

        self.add_step_btn = QPushButton("⚑  Add Step Marker")
        self.add_step_btn.setObjectName("AccentButton")
        self.add_step_btn.setToolTip(
            "Add the current step number/text to the recording timeline and video overlay. This does not draw on the viewer."
        )

        step_layout.addRow("☷  Step number", self.step_number_spin)
        step_layout.addRow("", self.auto_increment_check)
        step_layout.addRow("○  Narrative", self.narrative_check)
        step_layout.addRow("⚑  Marker text", self.step_input)
        step_layout.addRow("", self.add_step_btn)
        step_box.set_content_layout(step_layout)
        layout.addWidget(step_box)

        # ------------------------------------------------------------------
        # Live annotation
        # ------------------------------------------------------------------
        annotation_box = _CollapsibleSection("4  Live Annotation")
        annotation_layout = QVBoxLayout()
        annotation_layout.setContentsMargins(12, 12, 12, 10)
        annotation_layout.setSpacing(8)

        style_grid = QGridLayout()
        style_grid.setContentsMargins(0, 0, 0, 0)
        style_grid.setHorizontalSpacing(8)
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(list(PALETTES.keys()))
        self.transparent_shape_check = QCheckBox("Transparent circle / rectangle fill")
        self.transparent_shape_check.setToolTip(
            "Leave circle and rectangle interiors transparent instead of filled."
        )
        style_grid.addWidget(QLabel("◉  Palette"), 0, 0)
        style_grid.addWidget(self.palette_combo, 0, 1)
        style_grid.addWidget(self.transparent_shape_check, 1, 0, 1, 2)
        style_grid.setColumnStretch(2, 1)
        annotation_layout.addLayout(style_grid)

        mode_grid = QGridLayout()
        mode_grid.setContentsMargins(0, 0, 0, 0)
        mode_grid.setHorizontalSpacing(8)
        mode_grid.setVerticalSpacing(8)
        self.arrow_btn = QPushButton("↗ Arrow")
        self.text_stamp_btn = QPushButton("T Text")
        self.numbered_circle_btn = QPushButton("① Step")
        self.circle_btn = QPushButton("○ Circle")
        self.rectangle_btn = QPushButton("▭ Rectangle")
        self.move_annotation_btn = QPushButton("✥ Move")
        self.exit_drawing_btn = QPushButton("⌖ Exit")
        self.undo_annotation_btn = QPushButton("↶ Undo")
        self.redo_annotation_btn = QPushButton("↷ Redo")
        self.remove_overlay_btn = QPushButton("Remove Overlay")
        self.clear_annotations_btn = QPushButton("🗑 Clear")
        self.exit_drawing_btn.setToolTip(
            "Stop drawing mode and return mouse control to napari. Existing annotations stay visible."
        )
        self.clear_annotations_btn.setToolTip(
            "Delete all current annotation marks, but keep the overlay ready for more drawing."
        )
        self.remove_overlay_btn.setToolTip(
            "Remove the annotation overlay itself. This also clears overlay marks and restores napari control."
        )

        self.undo_annotation_btn.setShortcut("Ctrl+Z")
        self.redo_annotation_btn.setShortcut("Ctrl+Y")

        for button in (
            self.arrow_btn,
            self.text_stamp_btn,
            self.numbered_circle_btn,
            self.circle_btn,
            self.rectangle_btn,
            self.move_annotation_btn,
            self.exit_drawing_btn,
            self.undo_annotation_btn,
            self.redo_annotation_btn,
            self.clear_annotations_btn,
        ):
            button.setObjectName("AnnotationButton")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.remove_overlay_btn.setObjectName("SecondaryButton")

        mode_grid.addWidget(self.arrow_btn, 0, 0)
        mode_grid.addWidget(self.text_stamp_btn, 0, 1)
        mode_grid.addWidget(self.numbered_circle_btn, 0, 2)
        mode_grid.addWidget(self.circle_btn, 1, 0)
        mode_grid.addWidget(self.rectangle_btn, 1, 1)
        mode_grid.addWidget(self.move_annotation_btn, 1, 2)
        mode_grid.addWidget(self.exit_drawing_btn, 2, 0)
        mode_grid.addWidget(self.undo_annotation_btn, 2, 1)
        mode_grid.addWidget(self.redo_annotation_btn, 2, 2)
        mode_grid.addWidget(self.clear_annotations_btn, 3, 0)
        mode_grid.addWidget(self.remove_overlay_btn, 3, 1, 1, 2)
        annotation_layout.addLayout(mode_grid)

        hint = QLabel(
            "Exit stops drawing. Clear deletes marks. Remove Overlay unloads the overlay."
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        annotation_layout.addWidget(hint)
        annotation_box.set_content_layout(annotation_layout)
        layout.addWidget(annotation_box)

        status_box = _CollapsibleSection("5  Status / Activity")
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(12, 12, 12, 10)
        status_layout.setSpacing(8)

        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(8)
        self.drawing_state_chip = QLabel()
        self.annotation_visibility_chip = QLabel()
        self.napari_control_chip = QLabel()
        for chip in (
            self.drawing_state_chip,
            self.annotation_visibility_chip,
            self.napari_control_chip,
        ):
            chip.setObjectName("StatusChip")
            chip.setAlignment(Qt.AlignCenter)
            chip.setWordWrap(True)
            _allow_horizontal_shrink(chip)
            chips_row.addWidget(chip)
        status_layout.addLayout(chips_row)

        self.status_label = QLabel("Status: Idle")
        self.status_label.setObjectName("StatusLine")
        status_layout.addWidget(self.status_label)
        status_box.set_content_layout(status_layout)
        layout.addWidget(status_box)

        log_box = _CollapsibleSection("6  Log")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(12, 12, 12, 10)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(118)
        self.log_box.setObjectName("LogBox")
        log_layout.addWidget(self.log_box)
        log_box.set_content_layout(log_layout)
        layout.addWidget(log_box)

        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area, stretch=1)

        self._set_status_chips("off", "hidden", "ready")

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QWidget {
                color: #dce6f1;
            }
            DemoAssistantWidget {
                background: #111820;
                color: #dce6f1;
                font-size: 13px;
            }
            QScrollArea#MainScrollArea,
            QWidget#ScrollContent {
                background: #111820;
            }
            QLabel {
                color: #dce6f1;
            }
            QLabel#TitleLabel {
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#SectionTitle {
                color: #f2f8ff;
                font-size: 14px;
                font-weight: 700;
                padding-left: 2px;
            }
            QLabel#SubtitleLabel,
            QLabel#HintLabel,
            QLabel#ActivityLine,
            QLabel#StatusLine {
                color: #aeb9c7;
            }
            QLabel#ActivityLine {
                border-top: 1px solid #334250;
                padding-top: 6px;
            }
            QLabel#SectionLabel {
                color: #dce6f1;
                font-size: 15px;
                font-weight: 700;
                padding-left: 6px;
            }
            QGroupBox {
                border: 1px solid #334250;
                border-radius: 8px;
                margin-top: 10px;
                color: #dce6f1;
                font-size: 15px;
                font-weight: 700;
                background: rgba(255, 255, 255, 0.025);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QGroupBox#CollapsibleBody {
                border: 1px solid #2d5161;
                border-left: 4px solid #35d7f3;
                border-radius: 8px;
                margin-top: 2px;
                background: rgba(53, 215, 243, 0.035);
            }
            QWidget#SectionHeader {
                background: transparent;
            }
            QToolButton#SectionToggle {
                background: #121f28;
                border: 1px solid #35d7f3;
                border-radius: 6px;
                color: #7fe9ff;
                font-weight: 700;
                padding: 1px 5px;
            }
            QToolButton#SectionToggle:hover {
                background: #19303a;
                border-color: #ff75c8;
                color: #ff9bd8;
            }
            QLineEdit,
            QComboBox,
            QSpinBox,
            QTextEdit {
                background: #151e27;
                border: 1px solid #344354;
                border-radius: 5px;
                color: #e7eef7;
                padding: 5px 8px;
                selection-background-color: #207fb4;
            }
            QLineEdit:focus,
            QComboBox:focus,
            QSpinBox:focus,
            QTextEdit:focus {
                border-color: #35baf2;
            }
            QCheckBox {
                color: #dce6f1;
                spacing: 7px;
            }
            QPushButton {
                background: #202b36;
                border: 1px solid #3a4a5b;
                border-radius: 6px;
                color: #dce6f1;
                padding: 7px 10px;
                min-height: 24px;
            }
            QPushButton:hover {
                border-color: #5a7188;
                background: #273542;
            }
            QPushButton:disabled {
                color: #778392;
                background: #17202a;
                border-color: #283542;
            }
            QPushButton#HeaderIconButton {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                padding: 0;
                border-radius: 13px;
                color: #aeb9c7;
            }
            QPushButton#StartButton {
                background: #154f24;
                border-color: #38b24e;
                color: #e8ffe9;
                font-weight: 700;
            }
            QPushButton#StartButton:hover {
                background: #1d6530;
            }
            QPushButton#PauseButton {
                background: #25303b;
                border-color: #455667;
                color: #dce6f1;
                font-weight: 700;
            }
            QPushButton#StopButton {
                background: #4a2024;
                border-color: #b14a53;
                color: #ff9da6;
                font-weight: 700;
            }
            QPushButton#SnapshotButton {
                background: #152f3b;
                border-color: #35baf2;
                color: #b8f2ff;
                font-weight: 700;
            }
            QPushButton#SnapshotButton:hover {
                background: #1c4050;
                border-color: #ff75c8;
                color: #ffd2ee;
            }
            QPushButton#AnnotationButton {
                border-color: #435365;
                min-height: 36px;
            }
            QPushButton#AnnotationButton:hover {
                border-color: #35d7f3;
                color: #7fe9ff;
            }
            QPushButton#AccentButton {
                border-color: #d63aa3;
                color: #ff75c8;
                background: rgba(214, 58, 163, 0.10);
            }
            QPushButton#SecondaryButton {
                color: #aeb9c7;
            }
            QLabel#StatusChip {
                border-radius: 5px;
                padding: 7px 10px;
                font-weight: 700;
            }
            QLabel#StatusChip[state="blue"] {
                color: #84cfff;
                border: 1px solid #2f8bd7;
                background: rgba(47, 139, 215, 0.12);
            }
            QLabel#StatusChip[state="green"] {
                color: #8ff29a;
                border: 1px solid #43bb55;
                background: rgba(67, 187, 85, 0.12);
            }
            QLabel#StatusChip[state="gray"] {
                color: #aeb9c7;
                border: 1px solid #3a4a5b;
                background: rgba(255, 255, 255, 0.035);
            }
            QLabel#StatusChip[state="amber"] {
                color: #ffd86c;
                border: 1px solid #bd9730;
                background: rgba(189, 151, 48, 0.12);
            }
            QTextEdit#LogBox {
                background: #0f151d;
                border: 1px solid #334250;
                border-radius: 6px;
                color: #cdd7e3;
                font-family: monospace;
                font-size: 12px;
            }
            """
        )

    def _style_chip(self, chip: QLabel, text: str, state: str):
        chip.setText(text)
        chip.setProperty("state", state)
        chip.style().unpolish(chip)
        chip.style().polish(chip)

    def _set_status_chips(
        self,
        drawing_state: str,
        annotation_visibility: str,
        napari_control_state: str,
    ):
        drawing_map = {
            "off": ("◌  Drawing Off", "blue"),
            "arrow": ("↗  Arrow Mode", "amber"),
            "text": ("T  Text Mode", "amber"),
            "numbered_circle": ("①  Step Mode", "amber"),
            "circle": ("○  Circle Mode", "amber"),
            "rectangle": ("▭  Rectangle Mode", "amber"),
            "move": ("✥  Move Mode", "amber"),
        }
        visibility_map = {
            "hidden": ("◉  Annotations Hidden", "gray"),
            "visible": ("◉  Annotations Visible", "green"),
        }
        control_map = {
            "ready": ("✓  Napari Ready", "blue"),
            "drawing": ("⌖  Drawing Active", "amber"),
        }

        self._style_chip(
            self.drawing_state_chip,
            *drawing_map.get(drawing_state, drawing_map["off"]),
        )
        self._style_chip(
            self.annotation_visibility_chip,
            *visibility_map.get(annotation_visibility, visibility_map["hidden"]),
        )
        self._style_chip(
            self.napari_control_chip,
            *control_map.get(napari_control_state, control_map["ready"]),
        )

    def _connect_signals(self):
        self.about_btn.clicked.connect(self.show_about)
        self.choose_output_btn.clicked.connect(self.choose_output_path)
        self.start_btn.clicked.connect(self.start_recording)
        self.snapshot_btn.clicked.connect(self.save_png_snapshot)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.add_step_btn.clicked.connect(self.add_step_marker)

        self.arrow_btn.clicked.connect(lambda: self.set_annotation_mode("arrow"))
        self.text_stamp_btn.clicked.connect(lambda: self.set_annotation_mode("text"))
        self.numbered_circle_btn.clicked.connect(lambda: self.set_annotation_mode("numbered_circle"))
        self.circle_btn.clicked.connect(lambda: self.set_annotation_mode("circle"))
        self.rectangle_btn.clicked.connect(lambda: self.set_annotation_mode("rectangle"))
        self.move_annotation_btn.clicked.connect(lambda: self.set_annotation_mode("move"))
        self.exit_drawing_btn.clicked.connect(lambda: self.set_annotation_mode("off"))
        self.undo_annotation_btn.clicked.connect(self.undo_annotation)
        self.redo_annotation_btn.clicked.connect(self.redo_annotation)
        self.remove_overlay_btn.clicked.connect(self.deactivate_annotation_overlay)
        self.clear_annotations_btn.clicked.connect(self.clear_annotations)
        self.palette_combo.currentTextChanged.connect(self.set_annotation_palette)
        self.transparent_shape_check.toggled.connect(self._on_shape_fill_toggled)
        self.action_log_check.toggled.connect(self._on_action_logging_toggled)

        self.step_input.textChanged.connect(self._update_overlay_annotation_settings)
        self.narrative_check.toggled.connect(self._update_overlay_annotation_settings)
        self.step_number_spin.valueChanged.connect(self._update_overlay_number)

    def _on_action_logging_toggled(self, _checked: bool):
        self._refresh_event_filter()

    def _load_settings(self):
        last_output_path = self.settings.value("last_output_path", "", type=str)
        if last_output_path:
            self.output_line.setText(last_output_path)
            self.output_path = Path(last_output_path)

        last_palette = self.settings.value("last_palette", "Orange / Yellow", type=str)
        if last_palette in PALETTES:
            self.palette_combo.setCurrentText(last_palette)

        self.transparent_shape_check.setChecked(
            self.settings.value("transparent_shape_fill", False, type=bool)
        )
        self.target_combo.setCurrentText(
            self.settings.value("target", "Full napari window", type=str)
        )
        self.fps_spin.setValue(self.settings.value("fps", 12, type=int))
        self.crf_spin.setValue(self.settings.value("crf", 28, type=int))

    def _save_settings(self):
        self.settings.setValue("target", self.target_combo.currentText())
        self.settings.setValue("fps", self.fps_spin.value())
        self.settings.setValue("crf", self.crf_spin.value())
        self.settings.setValue("last_palette", self.palette_combo.currentText())
        self.settings.setValue(
            "transparent_shape_fill",
            self.transparent_shape_check.isChecked(),
        )
        output_text = self.output_line.text().strip()
        if output_text:
            self.settings.setValue("last_output_path", output_text)
            self.settings.setValue("last_output_dir", str(Path(output_text).parent))

    def _set_idle_state(self):
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._recording_paused = False
        self.pause_btn.setText("Ⅱ Pause")
        self._update_undo_redo_buttons()

    def _set_recording_state(self):
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self._recording_paused = False
        self.pause_btn.setText("Ⅱ Pause")

    def _log(self, text: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.append(f"[{timestamp}] {text}")

    def show_about(self):
        try:
            version = metadata.version(PACKAGE_NAME)
        except metadata.PackageNotFoundError:
            version = "1.5.0"

        message = QMessageBox(self)
        message.setWindowTitle("About napari-demo-assistant")
        message.setIcon(QMessageBox.Information)
        message.setText(
            "<b>napari-demo-assistant</b><br>"
            f"Version: {version}<br>"
            "Author: Wulin Teo"
        )
        message.setInformativeText(
            "Record napari demos with arrows, labels, and numbered steps.\n\n"
            f"Bug reports:\n{BUG_REPORT_URL}"
        )
        message.setStandardButtons(QMessageBox.Ok)
        if hasattr(message, "exec"):
            message.exec()
        else:
            message.exec_()

    def choose_output_path(self):
        start_path = self.output_line.text().strip()
        if not start_path:
            last_dir = self.settings.value("last_output_dir", str(Path.home()), type=str)
            start_path = str(Path(last_dir) / "napari_demo.mp4")

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose output video path",
            start_path,
            "MP4 Video (*.mp4)",
        )
        if not path:
            return

        if not path.lower().endswith(".mp4"):
            path += ".mp4"

        self.output_line.setText(path)
        self.output_path = Path(path)
        self._save_settings()
        self._log(f"Output selected: {path}")

    def _default_output_path(self) -> Path:
        last_dir = self.settings.value("last_output_dir", str(Path.home()), type=str)
        return Path(last_dir) / f"napari_demo_{time.strftime('%Y%m%d_%H%M%S')}.mp4"

    def _default_snapshot_path(self) -> Path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_text = self.output_line.text().strip()
        if output_text:
            output_path = Path(output_text)
            return output_path.with_name(f"{output_path.stem}_snapshot_{timestamp}.png")

        last_dir = self.settings.value("last_output_dir", str(Path.home()), type=str)
        return Path(last_dir) / f"napari_demo_snapshot_{timestamp}.png"

    def save_png_snapshot(self):
        try:
            if self.overlay is not None:
                self._sync_overlay_geometry()
                QApplication.processEvents()

            bbox = self._get_capture_bbox()
            self._preflight_capture_bbox(bbox)
            snapshot_path = self._default_snapshot_path()
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)

            with mss.mss() as sct:
                image = sct.grab(bbox)
                mss.tools.to_png(image.rgb, image.size, output=str(snapshot_path))

            self._save_settings()
            self._log(f"PNG snapshot saved: {snapshot_path}")
            QMessageBox.information(
                self,
                "Snapshot saved",
                f"Saved PNG snapshot:\n{snapshot_path}",
            )
        except Exception as exc:
            self._log(f"PNG snapshot failed: {exc}")
            QMessageBox.critical(self, "Snapshot failed", str(exc))

    def activate_annotation_overlay(self):
        # Live annotation covers the full napari window so arrows/stamps can
        # point to both the viewer and the plugin controls. During drawing,
        # right-click or Esc exits immediately and restores normal interaction.
        target_widget = self._get_annotation_target_widget()

        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None

        self.overlay_target_widget = target_widget
        self.overlay = AnnotationOverlay(parent=target_widget)
        self.overlay.setGeometry(target_widget.rect())
        self.overlay.set_palette(self.palette_combo.currentText())
        self.overlay.set_shape_fill_transparent(self.transparent_shape_check.isChecked())
        self.overlay.set_current_number(self.step_number_spin.value())
        self.overlay.set_narrative_text(self.step_input.text(), self.narrative_check.isChecked())
        self.overlay.set_mode("off")
        self.overlay.drawing_exited.connect(self._on_overlay_drawing_exited)
        self.overlay.number_used.connect(self._on_overlay_number_used)
        self.overlay.annotation_changed.connect(self._update_undo_redo_buttons)

        self._install_click_event_filter()
        self._update_undo_redo_buttons()
        self._set_status_chips("off", "visible", "ready")
        self.status_label.setText("Status: Annotation ready. Drawing is off.")
        self._log("Annotation ready. Drawing is off.")

    def deactivate_annotation_overlay(self):
        if self.overlay is not None:
            self._cleanup_annotation_overlay(
                update_ui=True,
                log_message="Annotation overlay removed.",
            )
            self._refresh_event_filter()
        else:
            self._log("No annotation overlay is active.")

    def set_annotation_mode(self, mode: str):
        if self.overlay is None:
            self.activate_annotation_overlay()

        if self.overlay is None or self.overlay_target_widget is None:
            self._log("Could not activate annotation overlay.")
            return

        self._update_overlay_annotation_settings()
        self._update_overlay_number()
        self._sync_overlay_geometry()
        self.overlay.set_mode(mode)

        if mode == "off":
            self._set_status_chips("off", "visible", "ready")
            self.status_label.setText("Status: Drawing off. Napari control restored.")
            self._log("Drawing off. Napari control restored.")
        elif mode == "arrow":
            self._set_status_chips("arrow", "visible", "drawing")
            self.status_label.setText("Status: Arrow mode")
            self._log("Arrow mode: drag tail to head. Right-click/Esc to exit.")
        elif mode == "text":
            self._set_status_chips("text", "visible", "drawing")
            self.status_label.setText("Status: Text mode")
            self._log("Text mode: click to place text. Right-click/Esc to exit.")
        elif mode == "numbered_circle":
            self._set_status_chips("numbered_circle", "visible", "drawing")
            self.status_label.setText("Status: Step mode")
            self._log("Step mode: click to place number. Right-click/Esc to exit.")
        elif mode == "circle":
            self._set_status_chips("circle", "visible", "drawing")
            self.status_label.setText("Status: Circle mode")
            self._log("Circle mode: drag center to edge. Right-click/Esc to exit.")
        elif mode == "rectangle":
            self._set_status_chips("rectangle", "visible", "drawing")
            self.status_label.setText("Status: Rectangle mode")
            self._log("Rectangle mode: drag corner to corner. Right-click/Esc to exit.")
        elif mode == "move":
            self._set_status_chips("move", "visible", "drawing")
            self.status_label.setText("Status: Move mode")
            self._log("Move mode: drag an existing annotation. Right-click/Esc to exit.")

    def set_annotation_palette(self, palette_name: str):
        self._save_settings()
        if self.overlay is not None:
            self.overlay.set_palette(palette_name)
        self._log(f"Palette selected: {palette_name}")

    def _on_shape_fill_toggled(self, checked: bool):
        self._save_settings()
        if self.overlay is not None:
            self.overlay.set_shape_fill_transparent(checked)
        state = "transparent" if checked else "filled"
        self._log(f"Circle/rectangle fill: {state}")

    def _update_overlay_annotation_settings(self):
        if self.overlay is not None:
            self.overlay.set_narrative_text(
                self.step_input.text(),
                self.narrative_check.isChecked(),
            )

    def _update_overlay_number(self):
        if self.overlay is not None:
            self.overlay.set_current_number(self.step_number_spin.value())

    def _on_overlay_number_used(self, used_number: int):
        if self.auto_increment_check.isChecked():
            self.step_number_spin.setValue(used_number + 1)

    def clear_annotations(self):
        if self.overlay is not None:
            self.overlay.clear_annotations()
            self._update_undo_redo_buttons()
            self._log("Annotations cleared.")
        else:
            self._log("No annotation overlay is active.")

    def undo_annotation(self):
        if self.overlay is None:
            self._log("No annotation overlay is active.")
            return

        if self.overlay.undo_last_annotation():
            self._log("Undo.")
        else:
            self._log("Nothing to undo.")
        self._update_undo_redo_buttons()

    def redo_annotation(self):
        if self.overlay is None:
            self._log("No annotation overlay is active.")
            return

        if self.overlay.redo_last_annotation():
            self._log("Redo.")
        else:
            self._log("Nothing to redo.")
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self):
        has_overlay = self.overlay is not None
        self.undo_annotation_btn.setEnabled(has_overlay and self.overlay.can_undo())
        self.redo_annotation_btn.setEnabled(has_overlay and self.overlay.can_redo())

    def start_recording(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "Recording active", "Recording is already running.")
            return

        output_text = self.output_line.text().strip()
        if output_text:
            self.output_path = Path(output_text)
        else:
            self.output_path = self._default_output_path()
            self.output_line.setText(str(self.output_path))

        if self.keep_output_check.isChecked():
            self._save_settings()

        try:
            bbox = self._get_capture_bbox()
            self._log(
                "Capture region: "
                f"left={bbox['left']}, top={bbox['top']}, "
                f"width={bbox['width']}, height={bbox['height']}"
            )
            self._preflight_capture_bbox(bbox)
        except Exception as exc:
            QMessageBox.critical(self, "Capture Error", str(exc))
            self._log(f"Capture region error: {exc}")
            return

        self.steps = []
        self.actions = []
        self.current_step_text = ""
        self._last_logged_action_key = None
        self._last_logged_action_time = 0.0

        self.worker = ScreenRecorderWorker(
            output_path=self.output_path,
            bbox=bbox,
            fps=self.fps_spin.value(),
            crf=self.crf_spin.value(),
            preset="veryfast",
            overlay_enabled=self.video_text_overlay_check.isChecked(),
            overlay_text_getter=self._get_current_step_text,
        )

        self.worker.status_changed.connect(self._on_status_changed)
        self.worker.frame_captured.connect(self._on_frame_captured)
        self.worker.recording_finished.connect(self._on_recording_finished)
        self.worker.recording_failed.connect(self._on_recording_failed)
        self.worker.start()

        self._set_recording_state()
        self._refresh_event_filter()
        if self.action_log_check.isChecked():
            self._record_action_from_widget(self.start_btn)
        self._log(f"Recording started: {self.output_path}")
        self._log(f"Compression: H.264 CRF={self.crf_spin.value()}, FPS={self.fps_spin.value()}")

    def toggle_pause(self):
        if self.worker is None:
            return

        if not self._recording_paused:
            self.worker.pause()
            self._recording_paused = True
            self.pause_btn.setText("▶ Resume")
        else:
            self.worker.resume()
            self._recording_paused = False
            self.pause_btn.setText("Ⅱ Pause")

    def stop_recording(self):
        if self.worker is not None:
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self._log("Stopping recording...")

    def add_step_marker(self):
        text = self.step_input.text().strip()
        number = self.step_number_spin.value()

        if text:
            marker_text = f"Step {number}: {text}"
        else:
            marker_text = f"Step {number}"

        self.current_step_text = marker_text

        if self.worker is None or not self.worker.isRunning():
            self._log(f"Timeline step prepared: {marker_text}")
            return

        elapsed = self.worker.elapsed_sec
        frame_index = self.worker.frame_index

        marker = StepMarker(
            time_sec=round(elapsed, 3),
            text=marker_text,
            frame_index=frame_index,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.steps.append(marker)
        self._log(f"Timeline step added at {elapsed:0.2f}s / frame {frame_index}: {marker_text}")

    def closeEvent(self, event):
        self._save_settings()
        self._cleanup_annotation_overlay(update_ui=False)
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
        super().closeEvent(event)

    def _get_current_step_text(self) -> str:
        return self.current_step_text

    def _get_target_widget(self):
        target = self.target_combo.currentText()

        if target == "Full napari window":
            return self.viewer.window._qt_window

        if target == "Viewer canvas only":
            return self.viewer.window.qt_viewer.canvas.native

        raise ValueError(f"Unknown recording target: {target}")

    def _get_annotation_target_widget(self):
        return self.viewer.window._qt_window

    def _get_capture_bbox(self) -> dict:
        widget = self._get_target_widget()
        widget.repaint()

        rect = widget.rect()
        top_left = widget.mapToGlobal(rect.topLeft())

        left = int(top_left.x())
        top = int(top_left.y())
        width = int(rect.width())
        height = int(rect.height())

        if width <= 0 or height <= 0:
            raise RuntimeError(
                "Could not determine a valid napari capture region. "
                "Make sure napari is visible on screen."
            )

        return {"left": left, "top": top, "width": width, "height": height}

    def _preflight_capture_bbox(self, bbox: dict):
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                monitor = monitors[0]
                self._log(
                    "Screen bounds: "
                    f"left={monitor.get('left', 0)}, top={monitor.get('top', 0)}, "
                    f"width={monitor['width']}, height={monitor['height']}"
                )
                sct.grab(bbox)
        except Exception as exc:
            raise RuntimeError(
                "The selected recording area could not be captured.\n\n"
                "Try one of these:\n"
                "- Resize the napari window smaller.\n"
                "- Move the window fully onto the monitor.\n"
                '- Select "Viewer canvas only" instead of "Full napari window".\n'
                "- Lower FPS if recording is slow.\n\n"
                f"Capture region: left={bbox['left']}, top={bbox['top']}, "
                f"width={bbox['width']}, height={bbox['height']}\n"
                f"Recorder error: {exc}"
            ) from exc

    def _on_overlay_drawing_exited(self):
        self._set_status_chips("off", "visible", "ready")
        self.status_label.setText("Status: Drawing off. Napari control restored.")
        self._update_undo_redo_buttons()

    def _on_status_changed(self, status: str):
        self.status_label.setText(f"Status: {status}")

    def _on_frame_captured(self, frame_index: int, elapsed: float):
        self.frame_label.setText(f"▣  Frames: {frame_index}   •   ◷  Time: {elapsed:0.1f} s")

    def _on_recording_finished(self, path: str):
        self._set_idle_state()
        self._save_steps_json()
        self._save_actions_json()
        self._save_srt_captions()
        self._save_settings()
        QTimer.singleShot(0, self._refresh_event_filter)
        self._log(f"Recording finished: {path}")

        QMessageBox.information(
            self,
            "Recording finished",
            f"Saved video:\n{path}\n\nSaved step markers, action log, and captions beside the video.",
        )

    def _on_recording_failed(self, error_text: str):
        self._set_idle_state()
        QTimer.singleShot(0, self._refresh_event_filter)
        self._log("Recording failed.")
        self._log(error_text)
        QMessageBox.critical(self, "Recording failed", error_text)

    def _save_steps_json(self):
        if self.output_path is None:
            return

        steps_path = self.output_path.with_suffix(".steps.json")
        payload = {
            "schema_version": "0.3",
            "video_path": str(self.output_path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fps": self.fps_spin.value(),
            "crf": self.crf_spin.value(),
            "target": self.target_combo.currentText(),
            "steps": [asdict(step) for step in self.steps],
        }

        try:
            with open(steps_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._log(f"Step markers saved: {steps_path}")
        except Exception as exc:
            self._log(f"Failed to save step markers: {exc}")

    def _save_actions_json(self):
        if self.output_path is None:
            return

        if not self.action_log_check.isChecked() and not self.actions:
            return

        actions_path = self.output_path.with_suffix(".actions.json")
        payload = {
            "schema_version": "0.1",
            "video_path": str(self.output_path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fps": self.fps_spin.value(),
            "crf": self.crf_spin.value(),
            "target": self.target_combo.currentText(),
            "actions": [asdict(action) for action in self.actions],
        }

        try:
            with open(actions_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._log(f"Action log saved: {actions_path}")
        except Exception as exc:
            self._log(f"Failed to save action log: {exc}")

    def _save_srt_captions(self):
        if self.output_path is None or not self.srt_export_check.isChecked():
            return

        cues = self._build_srt_cues()
        if not cues:
            self._log("No caption cues to save.")
            return

        srt_path = self.output_path.with_suffix(".srt")
        try:
            with open(srt_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self._format_srt(cues))
            self._log(f"YouTube SRT captions saved: {srt_path}")
        except Exception as exc:
            self._log(f"Failed to save SRT captions: {exc}")

    def _build_srt_cues(self) -> list[tuple[float, str]]:
        cues: list[tuple[float, str]] = []

        for step in self.steps:
            text = self._sanitize_caption_text(step.text)
            if text:
                cues.append((float(step.time_sec), text))

        if self.srt_actions_check.isChecked():
            for action in self.actions:
                text = self._sanitize_caption_text(action.text)
                if text:
                    cues.append((float(action.time_sec), text))

        cues.sort(key=lambda cue: cue[0])
        return cues

    def _format_srt(self, cues: list[tuple[float, str]]) -> str:
        blocks = []
        for index, (start, text) in enumerate(cues, start=1):
            end = self._caption_end_time(cues, index - 1)
            blocks.append(
                f"{index}\n"
                f"{self._format_srt_timestamp(start)} --> {self._format_srt_timestamp(end)}\n"
                f"{text}"
            )
        return "\n\n".join(blocks) + "\n"

    def _caption_end_time(self, cues: list[tuple[float, str]], index: int) -> float:
        start = max(0.0, cues[index][0])
        default_end = start + CAPTION_DURATION_SEC

        if index + 1 >= len(cues):
            return default_end

        next_start = max(0.0, cues[index + 1][0])
        if next_start <= start:
            return start + 1.0

        return min(default_end, max(start + 1.0, next_start - 0.1))

    def _format_srt_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _sanitize_caption_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        return text.replace("<", "").replace(">", "")
