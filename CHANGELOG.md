# Changelog

## 1.3.0 — 2026-11-05

- **Browser graphics mode (Ctrl+G)**: the text view stays as it was (own HTML parser,
  no JavaScript); `Ctrl+G` shows the same page as real pixels from a headless
  Chromium/Brave over the DevTools protocol. Frames arrive only when the page
  changes (Page.startScreencast, acked at most ~20/s), mouse and keyboard go back in
  as real input events, the pointer is drawn locally and updated row by row.
  Starts in the configured retro look at a quarter resolution (Chromium then renders
  only a quarter of the screen for us); `Ctrl+O`/`o` switches to the original in full
  resolution and colour.
  Address bar in the status line (`Ctrl+L`), history (`Alt+Left/Right`, `Backspace`),
  reload (`Ctrl+R`/`F5`), copy (`Ctrl+C`) and the monocell clipboard (`Ctrl+V`),
  clickable links/fields/buttons, double clicks, wheel and middle-button scrolling.
  Single-letter hotkeys act only outside text fields (`Ctrl` combinations always).
  The pointer follows the look: with a retro look it is drawn as 2x2 blocks (half a
  canvas pixel) in the darkest and brightest colours of that look's palette.
  Text from the text view is dropped: Chromium is now only the graphics mode.
  Ads/trackers blocked, fonts and pictures not; popups stay in the tab.
- **Original mode in every graphics app** (`o` in video, pictures, maps, browser):
  full display resolution in full colour - no palette, no dither, no upscale. The
  video graph and the still pipeline have a plain path for it (stills need no
  ffmpeg then).
- **Map**: `o` keeps the same area (two tile zoom levels up) and saves the normal
  zoom level again on leaving.
- **Fix**: mouse input in graphics mode crashed on the VT (`term.cols`/`term.rows`
  do not exist - maps and pictures dragged/zoomed nothing). Now `term.w`/`term.h`.
- **Fix**: the screen-size helper for the still pipeline centres and pads correctly
  (was only used at full size before).
- Tests: 535 (new: webgfx geometry/hotkeys/pointer, CDP key mapping, console font, partial
  row repaint, original-mode pipeline).

## 1.2.0 — 2026-11-04

- **Writer** shows the printed page: 80×66 sheet with margins, page numbers and
  page breaks; FIGlet text for S2/S3 (Graceful/Big, selectable); `-`/`=` underline
  rows that push the following lines down; pictures anchored to a paragraph with
  text wrapping (`wrap=square`/`block`), drag to move, `#` corner to resize;
  monochrome page (one ink, no backgrounds); `Ctrl+P` prints PPDS for the IBM
  Proprinter with pictures as characters (black = paper). New: `TXT` export as
  plain text, real image embedding in `.docx`, `S1`/`U1`/`U2` toolbar buttons.
  No italic (neither the console nor the printer can show it).
- **Updates app** (System → Updates): list upgrades without root, refresh the
  package lists, install through `/usr/local/sbin/monocell-apt` with a sudoers
  rule (`deploy/x61s/install-extras.sh`); never removes packages, keeps local
  config files, shows progress and offers a reboot when needed.
- **Login screen** for `/etc/issue` with the MONOCELL wordmark in the theme
  colours, hostname, system, date and a hint: enter the user name, then scan the
  fingerprint. `pam_fprintd` gets 3 tries / 45 s (was 1 / 3 s on the X61s).
  The 3270 console font's `=` is repaired on the system copy.
- **Cell aspect from the console font**: the VT reports no pixel size, so video,
  pictures, PDF pages and the wallpaper derive the cell height/width from the
  actual font (Fixed 8×16 → 2.0, IBM 3270 9×16 → 1.78). A 4:3 film fills a 4:3
  screen without bars again.
- **Desktop**: start menu locks the screen (`vlock`) and suspends; hostname is
  right-aligned in the start menu header; battery warnings at 20/10/5 % and
  auto-suspend at 3 %; no mount popups by default (drives live in Files).
- **Files**: `Ctrl+F` searches names recursively below the current folder.
- New apps: Updates, Systemtest (21 hardware checks), About, Pi Benchmark long
  run, Sheets charts.
- 511 unit tests.

## 1.1.0 — 2026-11-01

- Graphics mode for the video player and picture viewer (`/dev/fb0`, VGA/EGA/
  CGA/amber/green looks), OpenStreetMap maps in the same pipeline.
- Files: remembers the last folder, removable media (mount/unmount), copy
  progress with cancel, async listing so slow shares cannot stall the UI.

## 1.0.0 — 2026-10-21

- First public release: cell compositor with truecolor/palette/VT backends,
  overlapping windows, taskbar, start menu, apps (Files, Browser, Terminal,
  Editor, Writer, Sheets, PDF, Calculator, Calendar, Mail, Viewer, Video, Maps,
  YouTube, Music, Snake, Jump, System, Network, Settings), themes, clipboard,
  text screenshots, 1.0 test suite.
