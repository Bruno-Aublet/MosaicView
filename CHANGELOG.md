# Changelog

## [1.0.4] - 2026-03-31

- Fixed a bug where resizing images in a CBZ containing a `ComicInfo.xml` with a `<Pages>` section would cause the application to become unresponsive for the entire duration of the operation. After each resized image, the XML metadata was updated and a signal was emitted from the worker thread, triggering a full rebuild of the metadata panel UI on each image. On a 110-page file this resulted in 110 queued UI rebuilds that could take over an hour to drain. The signal is now suppressed during the resize loop and emitted once when the operation completes.
- Fixed a long freeze at the end of a resize operation. The worker was unnecessarily invalidating the cached thumbnail pixmap for each resized image, forcing `render_mosaic` to rebuild all thumbnails from scratch in the UI thread. Since resizing does not change the visual appearance of thumbnails, the cache is now preserved.
- Fixed a long freeze after the mosaic was refreshed at the end of a resize operation. The metadata panel was doing a full rebuild of all its widgets (including all page rows) in response to the metadata signal. After a resize, only the `ImageWidth`, `ImageHeight`, and `ImageSize` values change — the panel now updates only those values in place.
- Fixed a bug where dragging a WebP image from a web browser would incorrectly store it with a `.jpg` extension. Chrome silently transcodes images to WebP when dragging them, while keeping the original filename. The actual file format is now detected from the file content and the extension is corrected accordingly.
- The file extension label in the mosaic now appears in red for image formats other than JPG, PNG, GIF, and BMP (e.g. WEBP, TIFF, ICO). This is a warning indicator: these formats may not be readable by older CBZ/CBR readers. MosaicView itself handles them normally. Non-image files (e.g. `.xml`, `.nfo`) are not affected.
- Fixed the "Conversion complete" dialog where the three action buttons were too narrow to display their text correctly. The dialog width was increased from 540px to 620px.
- On startup, stale `_MEI*` temporary directories left behind by PyInstaller after a crash are now silently deleted. Directories still locked by another active instance are left untouched. If a directory cannot be deleted after a crash, a Windows restart may be required to release the lock.
- Fixed a bug where dropping multiple archives onto an empty canvas would not show the source archive name in the thumbnail tooltips. Only the merge path (dropping onto an already-open comic) correctly set the provenance. The initial multi-load path now sets it as well.
- In split view, resizing the window now preserves the ratio between the two panels instead of forcing a 50/50 split. Double-clicking the separator resets it to 50/50.
- Reorganized the right-click context menus. Commands that act on a selection (Save selection as CBZ, Export selected pages, Print selection, Copy, Cut, Delete, Deselect) have been moved from the canvas context menu to the thumbnail context menu. Commands that act on the archive as a whole (Renumber pages, Flatten directories) have been moved from the thumbnail context menu to the canvas context menu. The Show/Hide icon column command is now the first item in the canvas context menu.

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
