# Changelog

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
