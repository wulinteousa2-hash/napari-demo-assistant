"""napari-demo-assistant."""

try:
    from ._widget import DemoAssistantWidget
except Exception:  # pragma: no cover
    # napari imports the widget lazily through the manifest. Keeping this guard
    # avoids import-time failures in metadata-only contexts.
    DemoAssistantWidget = None  # type: ignore

__all__ = ["DemoAssistantWidget"]
