# napari-demo-assistant

`napari-demo-assistant` records short napari workflow videos with live visual cues, including arrows, numbered step circles, and optional labels.

It is designed for scientific-software demos, user support, GitHub issue feedback, tutorials, and plugin workflows such as `napari-sam3-assistant`.

The plugin focuses on **demo recording**, not screenshot capture. Most operating systems already provide screenshot tools. The missing workflow is a simple napari-native way to record what happened and show users where to click.

## Key features

- Record the full napari window or only the viewer canvas.
- Export MP4 video with H.264 compression.
- Add arrows, text stamps, and numbered step circles while recording.
- Use high-contrast annotation color palettes.
- Attach optional narrative labels to annotations.
- Right-click or press `Esc` to exit drawing mode.
- Keep annotations visible after leaving drawing mode.
- Undo/redo annotations with buttons or `Ctrl+Z` / `Ctrl+Y`.
- Save timeline step markers beside the video as `.steps.json`.
- Remember the last output path and annotation color palette.

## Why this plugin exists

For interactive napari workflows, a short video is often clearer than written instructions. This is especially true for prompting, mask previews, label cleanup, layer switching, and plugin-specific controls.

Written documentation still matters, but UI changes make screenshots and step-by-step text expensive to maintain. A quick recording of the current napari workflow is often the fastest way to help users follow the correct steps.

`napari-demo-assistant` helps developers, imaging scientists, and support users record clear napari workflow videos with lightweight visual guidance. It is also useful for troubleshooting because users can show exactly what they clicked, what happened, and where an error appeared.

Commercial tools such as Snagit are useful, but they may not be available on Linux, remote workstations, or scientific Python environments. This plugin provides a focused napari-native alternative.

## Typical use cases

- Record short napari workflow tutorials for teaching, onboarding, or plugin documentation.
- Demonstrate plugin steps, button clicks, layer selection, and mask-cleanup actions.
- Create video replies for GitHub issues, user support, or collaborator feedback.
- Let users record bugs, unexpected behavior, or error messages so developers can see exactly what happened.

## Installation

### From PyPI

```bash
pip install napari-demo-assistant
```

### Development install

From the repository root:

```bash
pip install -e .
```

Then start napari:

```bash
napari
```

Open the plugin from:

```text
Plugins > Demo Assistant
```

## Basic workflow

1. Choose the recording target:
   - `Full napari window`
   - `Viewer canvas only`
2. Choose the output `.mp4` path.
3. Click `Start Recording`.
4. Add annotations when needed:
   - `Arrow`
   - `Text Stamp`
   - `Numbered Circle`
5. Right-click or press `Esc` to exit drawing mode.
6. Click `Stop` to finish recording.

If timeline step markers are used, a `.steps.json` file is saved beside the video.

## Annotation behavior

Live annotations can cover the full napari window, including plugin controls. This allows arrows and step markers to point to either the viewer or the user interface.

Drawing mode is intentionally easy to exit:

- Right-click exits drawing mode.
- `Esc` exits drawing mode.
- `Exit Drawing` exits drawing mode.
- Existing annotations stay visible.
- Napari mouse control is restored after drawing mode is off.

## Output notes

The plugin records MP4 video using `imageio-ffmpeg`, `mss`, OpenCV, and H.264 compression.

`CRF` controls compression:

- Lower CRF: higher quality, larger files.
- Higher CRF: smaller files, lower quality.

A practical default for demos is usually `CRF 28` at `12 FPS`.

## Limitations

- Intended for napari workflow recording, not general desktop screen capture.
- Audio recording is not included.
- Screenshot capture is not a major feature because most operating systems already provide it.
- Very large windows, high FPS, or low CRF values can produce large video files.

## License

This project is released under the MIT License.
