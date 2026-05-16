# napari-demo-assistant

`napari-demo-assistant` is a lightweight napari plugin for recording short workflow demonstration videos directly from a napari session.

It was built for practical scientific-software demos: showing users how a workflow works, explaining a GitHub issue, recording a tutorial, or demonstrating another napari plugin such as `napari-sam3-assistant`.

The plugin focuses on **demo recording**, not general screenshot capture. Most operating systems already provide screenshot tools. The missing workflow is a simple napari-friendly way to record a demo while adding visible click, arrow, and numbered-step cues so viewers can follow the action.

## Key features

- Record the full napari window or only the viewer canvas.
- Export directly to MP4 using H.264 compression.
- Add live arrows during recording.
- Add high-contrast numbered step circles.
- Add optional narrative labels beside arrows, text stamps, or step circles.
- Use right-click or `Esc` to exit drawing mode and return mouse control to napari.
- Undo and redo annotations with buttons or `Ctrl+Z` / `Ctrl+Y`.
- Save timestamped step markers beside the video as `.steps.json`.
- Remember the last output path and annotation color palette.

## Why this plugin exists

When supporting users, the most effective instruction is often not a long written document, but a short video showing the actual workflow. This is especially true for interactive napari workflows such as prompting, previewing masks, cleaning labels, switching layers, or using plugin-specific controls.

Written documentation is still important, but software interfaces change. Every UI update can require screenshots, text, and step-by-step instructions to be revised, which becomes labor-intensive for small scientific software projects. From the user’s perspective, the fastest path is often to watch the latest workflow directly inside napari and follow the same steps.

`napari-demo-assistant` was built for this purpose: to help developers, imaging scientists, and support users quickly record clear napari workflow videos with lightweight visual guidance, including arrows, numbered step circles, and optional narrative labels. It is also useful for troubleshooting: users can record what they clicked, what happened on screen, and where an error or unexpected behavior appeared. This makes feedback much easier to interpret than a written description alone, especially when the issue depends on UI state, layer selection, prompts, logs, or workflow order.

Commercial tools such as Snagit are useful for annotated screen recording, but they are not always available or convenient on Linux, remote workstations, or scientific Python environments. `napari-demo-assistant` provides a focused napari-native alternative for recording practical workflow demonstrations.
## Typical use cases

- Record a quick tutorial for a napari plugin.
- Show how to run a segmentation workflow.
- Demonstrate where a user should click in a multi-step workflow.
- Create a video response for GitHub issues or user support.
- Record teaching or onboarding material for microscopy/image-analysis workflows.

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
4. Use live annotation tools when needed:
   - `Arrow`
   - `Text Stamp`
   - `Numbered Circle`
5. Right-click or press `Esc` to leave drawing mode and return to normal napari interaction.
6. Click `Stop` to finish the recording.

A `.steps.json` file is saved beside the video when timeline step markers are used.

## Annotation behavior

The live annotation overlay can cover the full napari window, including plugin controls. This allows arrows and step markers to point to either the image viewer or the user interface.

To avoid trapping the user in drawing mode:

- Right-click exits drawing mode.
- `Esc` exits drawing mode.
- `Exit Drawing / Return to Napari` also exits drawing mode.
- Existing annotations stay visible after exiting drawing mode.
- Mouse control returns to napari after drawing mode is turned off.

## Output notes

The plugin records MP4 video using `imageio-ffmpeg`, `mss`, OpenCV, and H.264 compression. The `CRF` setting controls compression quality:

- Lower CRF = higher quality and larger files.
- Higher CRF = smaller files and lower quality.

A practical default for demos is usually `CRF 28` at `12 FPS`.

## Limitations

- This plugin is intended for napari workflow recording, not general desktop screen capture.
- Audio recording is not included.
- Screenshot capture is intentionally not a major feature because most operating systems already provide it.
- Very large windows, high FPS, or low CRF values can produce large video files.

## License

This project is released under the MIT License.
