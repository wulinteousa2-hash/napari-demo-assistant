# Changelog

All notable changes to `napari-demo-assistant` are documented here.

This project follows a simple changelog style:

- `Added` for new features
- `Changed` for behavior or UI changes
- `Fixed` for bug fixes
- `Notes` for release context

## 0.1.0 - 2026-05-16

Initial public release.

### Added

- Added a napari dock widget for recording workflow demonstration videos.
- Added full napari-window recording.
- Added viewer-canvas-only recording.
- Added MP4 export through H.264 encoding.
- Added configurable FPS and CRF controls.
- Added optional video text overlay showing elapsed time and current step text.
- Added timeline step markers saved beside the video as `.steps.json`.
- Added live annotation overlay for demo recording.
- Added arrow annotations.
- Added text stamp annotations.
- Added numbered step-circle annotations.
- Added optional narrative text attached to arrows, text stamps, and numbered circles.
- Added high-contrast annotation color palettes.
- Added right-click and `Esc` exit behavior so users can quickly return to normal napari interaction.
- Added annotation undo and redo through buttons and `Ctrl+Z` / `Ctrl+Y`.
- Added persistent settings for output path, recording target, FPS, CRF, and annotation palette.

### Notes

- The plugin is intentionally focused on demo video recording rather than screenshot capture.
- The motivating use case is creating clear napari workflow videos for tutorials, GitHub issue support, and plugin demonstrations such as `napari-sam3-assistant` workflows.
