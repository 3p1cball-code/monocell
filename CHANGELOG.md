# Changelog

## Unreleased

- The X61s session (`monocell-session`, `.profile` autostart, deploy scripts) no longer
  runs `monocell --lowmem`: shadows on, video at the configured frame rate. The switch
  itself still exists and now only turns shadows off.

## 1.4.1 — 2026-11-08

- `--lowmem` no longer caps video at 10 fps. With the new renderers even 204x109 cells
  play at the configured rate (24 fps on the X61s, about 40 % of one core); the player
  still drops frames by itself when the machine is busy. Before, video stayed choppy
  although the CPU had plenty of headroom.

## 1.4.0 — 2026-11-08

- **minimal-cell**, Monocell's own console font: 5x7 pixels (204x109 characters on
  the 1024x768 X61s, Terminus 6x12 gives 170x64) and a 5x8 cut with one more row of
  line spacing (204x96). All of ASCII and Latin-1 including the umlauts, box
  drawing (light and double, heavy/mixed drawn with the light shapes), shades and
  block elements that use the whole cell, arrows and UI symbols; accented letters of
  Latin Extended fall back to their base letter. Built from a pixel design by
  `tools/make_minimal_cell.py` (reproducible, checked by a test), offered as
  *minimal-cell 5x7 / 5x8* in Settings -> Appearance -> Theme -> Font.
- **Faster text-mode video and pictures**, needed for the many cells of minimal-cell.
  The image -> cell renderers (blend, pixel, ascii, mono, grey) run on whole arrays
  with numpy when it is installed - same cells as the pure-python loops, which stay
  as the fallback (`MONOCELL_NO_NUMPY=1`). The terminal flush compares rows in
  blocks of 8 cells and writes each span with one join; on the console it sends
  only the part of the colour that changes (`\e[34m` instead of `\e[0;1;34;40m`),
  about 30 % fewer bytes for the kernel to parse. X61s, 204x109 cells at 24 fps:
  render 43 -> 3 ms per frame, Monocell CPU 85 % (frames dropped) -> 37 %.
  `tools/vidbench.py` and `tools/stagebench.py` measure it.
- `Renderer.nearest` decides per colour bucket, not by the first colour seen in it -
  pictures no longer depend on the order in which colours were rendered.
- `tools/snap.py --psf` picks glyphs through the font's unicode table, so umlauts
  and symbols show correctly in fonts that do not keep Latin-1 at its code points.

## 1.3.2 — 2026-11-07

- **Writer for the dot-matrix printer** (IBM Proprinter X24E, PPDS). The page is the
  sheet: paper size, 10/12 cpi, 6/8 lpi and the margins are document settings
  (File -> Page setup), header and footer with `{page} {pages} {title} {date}`.
- **One place for the fonts**: Format -> Fonts sets the large/huge FIGlet faces per
  document (saved in a `<!-- writer: ... -->` line, invisible in other viewers) with
  a live preview and "about N letters per line"; the Writer's font entries in
  Settings -> Tools are gone. Old `3d-ascii` settings map to the new font.
- **New 3D font "Block 3D"** (and flat "Block"): 3x5 pixel letters at two cells per
  pixel with a one-cell diagonal extrusion - compact enough for real headings
  (about nine letters on a 68-column line), built by `tools/make_block_font.py`.
- **Menu bar** (F10, Alt+F/E/O/I/V) and a format bar: paragraph styles, bold, single
  and double underline, size, alignment, table, picture, fonts, plus a ruler.
- **Paragraphs** can be centered, right-aligned or justified (`{.center}` in the
  source, `Ctrl+E` cycles), **tables** are Markdown pipe tables drawn as a grid
  (`Tab` moves between cells), `<pagebreak>` / Ctrl+Enter starts a new sheet.
- **Printing**: quality draft/letter, page range, copies; pictures print as 24-pin
  bit images (180 dpi, Floyd-Steinberg, the text of those rows rasterised with them)
  or as characters, per picture (`G`) or for the whole job. Character set 2 is
  selected so the umlauts print. A test page checks alignment of text and graphics.
- **Print preview (F6)**: the sheets as dots on the framebuffer - exactly what the
  printer will put on paper, page by page, fit-width/whole page/180 dpi.
- **Find and replace** (Ctrl+F, F3), templates (letter, flyer, notes), TXT export as
  printed, DOCX export with tables, alignment and page breaks.
- **Smaller console fonts** for more characters: Terminus 6x12 (170x64 on the
  1024x768 X61s screen instead of 113x48), 8x14, 8x16 bold, 10x20 and 12x24 are
  bundled (OFL) and offered in Settings -> Appearance -> Theme -> Font with their
  grid; ASCII pictures and video gain a lot of cells from this. The Writer's bit
  images print with a fine 8x16 font regardless of the screen font.
- **Smaller OSD in the graphics mode**: the info text and the bar of video, picture,
  map and browser now use 2x2 pixel blocks instead of 4x4 - half the size, and the
  same in the full (original) resolution. It no longer covers half the picture.
- **TRD audio fixed**: the server padded silence into the stream whenever the
  capture was only *late* (parec delivers in ~80 ms bursts), cutting holes into the
  sound - that was the intermittent static. It now lets the stream lag (the client
  buffers 400 ms) and only fills silence when the capture has really been quiet for
  400 ms; buffer trims never cut a sample in half. The client also only writes whole
  s16le frames to the player.
- **SIGUSR1 restarts monocell in place** (new code, same tty, no new login).
- Tests: 676.

## 1.3.1 — 2026-11-05

- **Zoom in the browser's graphics mode**: `+` / `-` (also `Alt++` / `Alt+-`, or
  `Ctrl++` in a terminal window) step through 50-300 %, `Alt+0` back to 100 %.
  Zooming in is the browser's page scale - the layout stays, the picture is magnified
  and stays sharp, the wheel pans it; zooming out uses a wider CSS viewport and the
  frames are scaled down. Outside text fields the bare keys zoom; while typing in a
  field they are typed. Stored as `web_zoom` (Settings -> Web).
- **Browser sound** in the graphics mode: Chromium no longer starts muted
  (`--mute-audio` was left over from the text engine). Video and audio play through
  the system output; playback still needs a click (autoplay is off). Verified on the
  X61s at the Pulse sink monitor.
- **Linux console**: Ctrl with a character key cannot be used - the console keymap
  turns `Ctrl++` into `Ctrl+^` and `Ctrl+-` into `Ctrl+_` (see
  `/etc/console-setup/cached_*.kmap`), so `Alt++`/`Alt+-` (Meta_plus, arrives as
  ESC + character) and the bare keys are the ones that work there.
- Tests: 538.

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
