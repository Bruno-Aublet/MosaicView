# Changelog

## [1.0.3] - 2026-03-29

- Fixed panel 2 always starting in light theme when the application was launched in dark mode with split view active.
- Fixed a ~600ms delay when switching between panels in split mode after flattening a comic with subdirectory structure. The active panel border was drawn using `setStyleSheet`, which caused Qt to recompute styles for all descendant widgets. Replaced with a `paintEvent`-based approach that draws the border directly without touching the style tree.
- Fixed a bug where the first character of the file name in the tab was partially or fully cut off after the 200px width cap was introduced.
- Fixed a bug where the hover tooltip on the file name tab was not following the mouse cursor and was not using the application's tooltip system. It now uses the same overlay tooltip as the rest of the application and correctly follows the cursor.
- Fixed a bug where the canvas and icon toolbar overlay tooltips did not update their colors when switching between light and dark mode.
- Fixed a bug where dragging an image onto a web page (e.g. Google Images reverse search) would result in an `ERR_FILE_NOT_FOUND` error. The temporary file was deleted as soon as the mouse button was released, before the browser had a chance to read it. The file is now kept until the comic is closed.
- Fixed a bug where temporary files were not cleaned up when closing a comic (only cleaned up on the second close, when the canvas was already empty).
- Fixed a bug where dragging a non-image file (e.g. `.nfo`) from one panel to another would show an incorrect "subdirectory structure" warning. Non-image files can now be dragged between panels. Within the same panel, non-image files are excluded from reordering (only images move). Dragging a mixed selection between panels moves everything; renumbering is only triggered if the selection contains at least one image.

## [1.0.2] - 2026-03-28

- Fixed a bug where the application could not be closed after dragging all images from panel 2 into panel 1, leaving panel 2 empty. Clicking the close button had no effect in that situation.
- Fixed a bug where dragging an image from a web browser onto panel 2 would add it to panel 1 instead, when panel 1 was the active panel at the time the download completed.
- The file name tab is now capped at 200px wide and truncated with an ellipsis when the file name is too long. The full name is shown in a tooltip on hover.
- Minor README updates: wording fix and added documentation for the automatic update check feature.

## [1.0.1] - 2026-03-26

- Added "Source code on GitHub" entry in the About menu and context menu, linking to the project repository. Translated into all 47 languages.
- Added "Check for updates" entry in the About menu and context menu. Compares the current version against the latest GitHub release and shows a download link if a newer version is available. Translated into all 47 languages.
- Added automatic update check at startup: if a newer version is found on GitHub, a notification banner appears below the tab bar and the About menu entry is highlighted in bold.

## [1.0.0] - 2026-03-25

First public release.
