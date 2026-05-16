from __future__ import annotations

import subprocess
import time
import traceback
from pathlib import Path

import cv2
import imageio_ffmpeg
import mss
import numpy as np
from qtpy.QtCore import QThread, Signal


class ScreenRecorderWorker(QThread):
    status_changed = Signal(str)
    frame_captured = Signal(int, float)
    recording_finished = Signal(str)
    recording_failed = Signal(str)

    def __init__(
        self,
        output_path: Path,
        bbox: dict,
        fps: int = 15,
        crf: int = 26,
        preset: str = "veryfast",
        overlay_enabled: bool = True,
        overlay_text_getter=None,
        parent=None,
    ):
        super().__init__(parent)
        self.output_path = Path(output_path)
        self.bbox = bbox
        self.fps = int(fps)
        self.crf = int(crf)
        self.preset = preset
        self.overlay_enabled = bool(overlay_enabled)
        self.overlay_text_getter = overlay_text_getter

        self._running = False
        self._paused = False
        self._frame_index = 0
        self._start_time = 0.0

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def elapsed_sec(self) -> float:
        if not self._start_time:
            return 0.0
        return time.time() - self._start_time

    def pause(self):
        self._paused = True
        self.status_changed.emit("Paused")

    def resume(self):
        self._paused = False
        self.status_changed.emit("Recording")

    def stop(self):
        self._running = False
        self.status_changed.emit("Stopping...")

    def run(self):
        self._running = True
        self._paused = False
        self._frame_index = 0
        self._start_time = time.time()

        ffmpeg_proc = None

        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

            width = int(self.bbox["width"])
            height = int(self.bbox["height"])

            if width <= 0 or height <= 0:
                raise ValueError(f"Invalid capture region: {self.bbox}")

            # H.264 requires even dimensions.
            width -= width % 2
            height -= height % 2
            self.bbox["width"] = width
            self.bbox["height"] = height

            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

            cmd = [
                ffmpeg_exe,
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(self.fps),
                "-i",
                "-",
                "-an",
                "-vcodec",
                "libx264",
                "-preset",
                self.preset,
                "-crf",
                str(self.crf),
                "-pix_fmt",
                "yuv420p",
                str(self.output_path),
            ]

            ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            frame_interval = 1.0 / max(self.fps, 1)
            self.status_changed.emit("Recording")

            with mss.mss() as sct:
                while self._running:
                    loop_start = time.time()

                    if self._paused:
                        time.sleep(0.05)
                        continue

                    raw = np.asarray(sct.grab(self.bbox))
                    frame = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                    frame = frame[:height, :width, :]

                    if self.overlay_enabled:
                        frame = self._draw_text_overlay(frame)

                    if ffmpeg_proc.stdin is not None:
                        ffmpeg_proc.stdin.write(frame.tobytes())

                    self._frame_index += 1
                    elapsed = self.elapsed_sec
                    self.frame_captured.emit(self._frame_index, elapsed)

                    spent = time.time() - loop_start
                    time.sleep(max(0.0, frame_interval - spent))

            self.status_changed.emit("Finalizing video...")

            if ffmpeg_proc.stdin is not None:
                ffmpeg_proc.stdin.close()

            ffmpeg_proc.wait(timeout=20)

            self.status_changed.emit("Finished")
            self.recording_finished.emit(str(self.output_path))

        except Exception:
            err = traceback.format_exc()
            self.recording_failed.emit(err)

        finally:
            if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass

    def _draw_text_overlay(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]

        elapsed = self.elapsed_sec
        elapsed_text = f"{elapsed:0.1f} s"

        step_text = ""
        if self.overlay_text_getter is not None:
            try:
                step_text = self.overlay_text_getter() or ""
            except Exception:
                step_text = ""

        lines = [elapsed_text]
        if step_text:
            lines.append(step_text)

        x = 20
        y = 35
        line_height = 32

        overlay = frame.copy()
        box_height = line_height * len(lines) + 18
        box_width = min(max(360, int(w * 0.45)), w - 40)

        cv2.rectangle(
            overlay,
            (10, 10),
            (10 + box_width, 10 + box_height),
            (0, 0, 0),
            -1,
        )

        frame = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)

        for i, line in enumerate(lines):
            cv2.putText(
                frame,
                line,
                (x, y + i * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        return frame
