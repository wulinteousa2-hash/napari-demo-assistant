from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from qtpy.QtCore import QEvent, QSettings, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ._overlay import PALETTES, AnnotationOverlay
from ._recorder import ScreenRecorderWorker


@dataclass
class StepMarker:
    time_sec: float
    text: str
    frame_index: int
    timestamp: str


class DemoAssistantWidget(QWidget):
    def __init__(self, napari_viewer):
        super().__init__()
        self.viewer = napari_viewer

        self.worker: Optional[ScreenRecorderWorker] = None
        self.steps: list[StepMarker] = []
        self.current_step_text = ""
        self.output_path: Optional[Path] = None

        self.overlay: Optional[AnnotationOverlay] = None
        self.overlay_target_widget: Optional[QWidget] = None
        self._click_event_filter_installed = False

        self.settings = QSettings("napari-demo-assistant", "napari-demo-assistant")

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._set_idle_state()

    def eventFilter(self, obj, event):
        """Draw transient click ripples for video emphasis without blocking Qt events."""
        if (
            self.overlay is not None
            and self.overlay_target_widget is not None
            and event.type() == QEvent.MouseButtonPress
            and hasattr(event, "button")
            and event.button() == Qt.LeftButton
        ):
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

        return super().eventFilter(obj, event)

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

    def _sync_overlay_geometry(self):
        if self.overlay is not None and self.overlay_target_widget is not None:
            self.overlay.setGeometry(self.overlay_target_widget.rect())
            self.overlay.raise_()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("<b>napari-demo-assistant</b>")
        subtitle = QLabel(
            "Record napari demos. Add arrows, optional narrative labels, and numbered step circles."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # ------------------------------------------------------------------
        # Recording
        # ------------------------------------------------------------------
        settings_box = QGroupBox("1. Recording")
        settings_layout = QFormLayout(settings_box)

        self.target_combo = QComboBox()
        self.target_combo.addItems(["Full napari window", "Viewer canvas only"])

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(12)

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(18, 35)
        self.crf_spin.setValue(28)

        self.video_text_overlay_check = QCheckBox("Show elapsed time / step text on recorded video")
        self.video_text_overlay_check.setChecked(True)

        self.output_line = QLineEdit()
        self.output_line.setPlaceholderText("Choose output .mp4 path")
        self.choose_output_btn = QPushButton("Choose")
        self.keep_output_check = QCheckBox("Keep selected output path")
        self.keep_output_check.setChecked(True)

        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_line, stretch=1)
        output_layout.addWidget(self.choose_output_btn)

        settings_layout.addRow("Target:", self.target_combo)
        settings_layout.addRow("FPS:", self.fps_spin)
        settings_layout.addRow("CRF:", self.crf_spin)
        settings_layout.addRow("Video overlay:", self.video_text_overlay_check)
        settings_layout.addRow("Output:", output_row)
        settings_layout.addRow("", self.keep_output_check)
        layout.addWidget(settings_box)

        # ------------------------------------------------------------------
        # Recording controls
        # ------------------------------------------------------------------
        controls_box = QGroupBox("2. Record Controls")
        controls_layout = QHBoxLayout(controls_box)
        self.start_btn = QPushButton("Start Recording")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        controls_layout.addWidget(self.start_btn)
        controls_layout.addWidget(self.pause_btn)
        controls_layout.addWidget(self.stop_btn)
        layout.addWidget(controls_box)

        # ------------------------------------------------------------------
        # Steps / narrative
        # ------------------------------------------------------------------
        step_box = QGroupBox("3. Step / Narrative")
        step_layout = QFormLayout(step_box)

        self.step_number_spin = QSpinBox()
        self.step_number_spin.setRange(1, 999)
        self.step_number_spin.setValue(1)

        self.auto_increment_check = QCheckBox("Auto-increment after numbered circle")
        self.auto_increment_check.setChecked(True)

        self.narrative_check = QCheckBox("Attach narrative text to arrow/text/circle")
        self.narrative_check.setChecked(False)

        self.step_input = QLineEdit()
        self.step_input.setPlaceholderText("Optional: Run SAM3 preview")

        self.add_step_btn = QPushButton("Add Timeline Step Marker")

        step_layout.addRow("Step number:", self.step_number_spin)
        step_layout.addRow("", self.auto_increment_check)
        step_layout.addRow("Narrative:", self.narrative_check)
        step_layout.addRow("Text:", self.step_input)
        step_layout.addRow("", self.add_step_btn)
        layout.addWidget(step_box)

        # ------------------------------------------------------------------
        # Live annotation
        # ------------------------------------------------------------------
        annotation_box = QGroupBox("4. Live Annotation")
        annotation_layout = QVBoxLayout(annotation_box)

        style_grid = QGridLayout()
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(list(PALETTES.keys()))
        style_grid.addWidget(QLabel("Color palette:"), 0, 0)
        style_grid.addWidget(self.palette_combo, 0, 1)
        annotation_layout.addLayout(style_grid)

        mode_grid = QGridLayout()
        self.arrow_btn = QPushButton("Arrow")
        self.text_stamp_btn = QPushButton("Text Stamp")
        self.numbered_circle_btn = QPushButton("Numbered Circle")
        self.exit_drawing_btn = QPushButton("Exit Drawing / Return to Napari")
        self.undo_annotation_btn = QPushButton("Undo Annotation")
        self.redo_annotation_btn = QPushButton("Redo Annotation")
        self.remove_overlay_btn = QPushButton("Remove Overlay")
        self.clear_annotations_btn = QPushButton("Clear Annotations")

        self.undo_annotation_btn.setShortcut("Ctrl+Z")
        self.redo_annotation_btn.setShortcut("Ctrl+Y")

        mode_grid.addWidget(self.arrow_btn, 0, 0)
        mode_grid.addWidget(self.text_stamp_btn, 0, 1)
        mode_grid.addWidget(self.numbered_circle_btn, 0, 2)
        mode_grid.addWidget(self.exit_drawing_btn, 1, 0, 1, 3)
        mode_grid.addWidget(self.undo_annotation_btn, 2, 0)
        mode_grid.addWidget(self.redo_annotation_btn, 2, 1)
        mode_grid.addWidget(self.clear_annotations_btn, 2, 2)
        mode_grid.addWidget(self.remove_overlay_btn, 3, 0, 1, 3)
        annotation_layout.addLayout(mode_grid)

        hint = QLabel(
            "Tip: Arrow/Text/Circle can annotate over the full napari window. Right-click or press Esc stops drawing but keeps existing annotations visible. Use Clear only when you want to remove marks. Clicks show a short ripple in the recording."
        )
        hint.setWordWrap(True)
        annotation_layout.addWidget(hint)
        layout.addWidget(annotation_box)

        self.status_label = QLabel("Status: Idle")
        self.frame_label = QLabel("Frames: 0 | Time: 0.0 s")
        layout.addWidget(self.status_label)
        layout.addWidget(self.frame_label)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(150)
        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.log_box)

    def _connect_signals(self):
        self.choose_output_btn.clicked.connect(self.choose_output_path)
        self.start_btn.clicked.connect(self.start_recording)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.add_step_btn.clicked.connect(self.add_step_marker)

        self.arrow_btn.clicked.connect(lambda: self.set_annotation_mode("arrow"))
        self.text_stamp_btn.clicked.connect(lambda: self.set_annotation_mode("text"))
        self.numbered_circle_btn.clicked.connect(lambda: self.set_annotation_mode("numbered_circle"))
        self.exit_drawing_btn.clicked.connect(lambda: self.set_annotation_mode("off"))
        self.undo_annotation_btn.clicked.connect(self.undo_annotation)
        self.redo_annotation_btn.clicked.connect(self.redo_annotation)
        self.remove_overlay_btn.clicked.connect(self.deactivate_annotation_overlay)
        self.clear_annotations_btn.clicked.connect(self.clear_annotations)
        self.palette_combo.currentTextChanged.connect(self.set_annotation_palette)

        self.step_input.textChanged.connect(self._update_overlay_annotation_settings)
        self.narrative_check.toggled.connect(self._update_overlay_annotation_settings)
        self.step_number_spin.valueChanged.connect(self._update_overlay_number)

    def _load_settings(self):
        last_output_path = self.settings.value("last_output_path", "", type=str)
        if last_output_path:
            self.output_line.setText(last_output_path)
            self.output_path = Path(last_output_path)

        last_palette = self.settings.value("last_palette", "Orange / Yellow", type=str)
        if last_palette in PALETTES:
            self.palette_combo.setCurrentText(last_palette)

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
        output_text = self.output_line.text().strip()
        if output_text:
            self.settings.setValue("last_output_path", output_text)
            self.settings.setValue("last_output_dir", str(Path(output_text).parent))

    def _set_idle_state(self):
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self._update_undo_redo_buttons()

    def _set_recording_state(self):
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setText("Pause")

    def _log(self, text: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.append(f"[{timestamp}] {text}")

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
        self.overlay.set_current_number(self.step_number_spin.value())
        self.overlay.set_narrative_text(self.step_input.text(), self.narrative_check.isChecked())
        self.overlay.set_mode("off")
        self.overlay.drawing_exited.connect(self._on_overlay_drawing_exited)
        self.overlay.number_used.connect(self._on_overlay_number_used)
        self.overlay.annotation_changed.connect(self._update_undo_redo_buttons)

        self._install_click_event_filter()
        self._update_undo_redo_buttons()
        self.status_label.setText("Status: Annotation ready | Drawing off | Existing marks stay visible")
        self._log("Annotation overlay prepared on the full napari window. Drawing is off; napari interaction is available. Existing annotations stay visible until Clear or Remove Overlay.")

    def deactivate_annotation_overlay(self):
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
            self.overlay_target_widget = None
            self._remove_click_event_filter()
            self._update_undo_redo_buttons()
            self.status_label.setText("Status: Annotation overlay removed")
            self._log("Annotation overlay removed.")
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
            self.status_label.setText("Status: Drawing off | Annotations visible | Napari interaction restored")
            self._log("Exit Drawing: annotations remain visible; napari receives mouse input normally. Right-click or Esc does the same.")
        elif mode == "arrow":
            self.status_label.setText("Status: Draw arrow | Drag tail to head")
            self._log("Arrow mode enabled. Drag from arrow tail to arrow head. Right-click or press Esc to exit; Ctrl+Z to undo.")
        elif mode == "text":
            self.status_label.setText("Status: Text stamp | Click to place text")
            self._log("Text stamp mode enabled. Text appears only when narrative is enabled and not empty. Right-click or press Esc to exit; Ctrl+Z to undo.")
        elif mode == "numbered_circle":
            self.status_label.setText("Status: Numbered circle | Click to place step number")
            self._log("Numbered circle mode enabled. Click to place the current step number. Right-click or press Esc to exit; Ctrl+Z to undo.")

    def set_annotation_palette(self, palette_name: str):
        self._save_settings()
        if self.overlay is not None:
            self.overlay.set_palette(palette_name)
        self._log(f"Annotation palette selected: {palette_name}")

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
            self._log("Undo annotation.")
        else:
            self._log("Nothing to undo.")
        self._update_undo_redo_buttons()

    def redo_annotation(self):
        if self.overlay is None:
            self._log("No annotation overlay is active.")
            return

        if self.overlay.redo_last_annotation():
            self._log("Redo annotation.")
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
        except Exception as exc:
            QMessageBox.critical(self, "Capture Error", str(exc))
            self._log(f"Capture region error: {exc}")
            return

        self.steps = []
        self.current_step_text = ""

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
        self._log(f"Recording started: {self.output_path}")
        self._log(f"Capture region: {bbox}")
        self._log(f"Compression: H.264 CRF={self.crf_spin.value()}, FPS={self.fps_spin.value()}")

    def toggle_pause(self):
        if self.worker is None:
            return

        if self.pause_btn.text() == "Pause":
            self.worker.pause()
            self.pause_btn.setText("Resume")
        else:
            self.worker.resume()
            self.pause_btn.setText("Pause")

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
        self._remove_click_event_filter()
        self.deactivate_annotation_overlay()
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

    def _on_overlay_drawing_exited(self):
        self.status_label.setText("Status: Drawing off | Annotations visible | Napari interaction restored")
        self._update_undo_redo_buttons()

    def _on_status_changed(self, status: str):
        self.status_label.setText(f"Status: {status}")

    def _on_frame_captured(self, frame_index: int, elapsed: float):
        self.frame_label.setText(f"Frames: {frame_index} | Time: {elapsed:0.1f} s")

    def _on_recording_finished(self, path: str):
        self._set_idle_state()
        self._save_steps_json()
        self._save_settings()
        self._log(f"Recording finished: {path}")

        QMessageBox.information(
            self,
            "Recording finished",
            f"Saved video:\n{path}\n\nSaved step markers beside the video.",
        )

    def _on_recording_failed(self, error_text: str):
        self._set_idle_state()
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
