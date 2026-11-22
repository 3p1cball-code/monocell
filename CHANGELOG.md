# Changelog

## Unreleased

- **Captive portals are found and opened by themselves.** A wifi that wants a login
  looks like a broken network from the inside: names resolve somewhere odd, requests
  come back with a page nobody asked for, HTTPS fails with `wrong version number`.
  Until now Monocell only offered two hard-coded railway addresses by hand - and on a
  train that is exactly the moment nothing works. The new `portal.py` asks the three
  addresses phones and laptops use for this purpose
  (`connectivitycheck.gstatic.com/generate_204`, `detectportal.firefox.com/success.txt`,
  `nmcheck.gnome.org/...`) and **reads** the answer instead of following it: a redirect
  names the login page, 511 means "authentication required", a page where a 204 or
  "success" belongs means somebody is in the way. Two probes have to agree, so a proxy
  that merely blocks one address does not turn into a login page that opens by itself.
  **Watch** hangs on `nmcli monitor`, probes at start and every 90 s, and on a portal
  puts a yellow `!` on the tray's network symbol, says so and opens the sign-in page in
  the browser's graphics mode - once per network, never again after the page was
  closed. When NetworkManager reports `full` again it says "online". The network app's
  `p`/the button **Anmeldeseite** probes the network itself and opens what it found
  (an open network says so and opens nothing, only when nothing answers is the
  gateway the last resort); the browser probes on its own when a page
  fails for network reasons (and offers *Find the network sign-in page* in its menu);
  a certificate nobody can verify - portals use their own CA - is retried once with
  `Security.setIgnoreCertificateErrors` instead of leaving Chromium's error page, which
  a headless tab cannot click through. `tests/test_portal.py` (28 tests).

## Unreleased

- **Programs in a terminal can copy again (OSC 52).** Modern TUI programs -
  opencode, vim, tmux, anything on a current toolkit - put a copy on the wire as
  `ESC ] 52 ; <selection> ; <base64>`, and the parser dropped that sequence on the
  floor: the app said "Copied to clipboard", Monocell's clipboard stayed empty. The
  VT now decodes it (BEL and `ESC \` terminators, base64 with or without padding,
  up to a megabyte) and hands the text to `wm.set_clipboard`: it lands in
  `~/.cache/monocell/clipboard`, in `monocell-clip`, in KDE Connect's *send
  clipboard* and - when Monocell runs inside a real terminal - in the surrounding
  terminal's clipboard, as before. A toast names the window the text came from.
  Read requests (`?`) are still not answered, child apps do not get the clipboard
  back. Strings that run past their limit (a DCS/APC flood) are now swallowed up to
  their terminator instead of spilling onto the screen as text. Five new tests in
  `tests/test_core.py`.

- **The ASCII look dithers its colours.** `ascii` art put one palette colour on
  every cell - the nearest hue - so a sky or a skin tone jumped between two entries
  in hard bands. Now the two palette entries that bracket a colour are mixed from
  cell to cell with the ordered matrix: the cells alternate, the way a sixteen
  colour picture has to. The brighter glyph keeps carrying the brightness. `d`
  cycles the dither in the video player too (`bayer4`, `bayer8`, `fs`, `none`; the
  picture viewer already had it, where it now really does something in `ascii`), and
  the player's button bar shows the current mode next to the render mode. `pixel`
  keeps its error diffusion, `blend` its ordered matrix, both unchanged.

- **The map has a rain radar (`w`).** The German weather service's radar composite
  RV (one kilometre, all of Germany plus a ring around it, three days of analysis
  and a two-hour nowcast, a frame every five minutes) is laid over the tiles as a
  transparent layer: one WMS `GetMap` in EPSG:3857 per view, in the fifteen class
  colours of the DWD legend, no key and no account. The classes are not painted
  solid but **graded by strength** (drizzle 4 %, downpour 100 %, `radar.ALPHA`) -
  at a quarter resolution even 0.2 mm/h otherwise washes a whole city green, which
  was measured against Hamburg (drizzle only) and Mainz (a real rain band). For the
  `VGA 256` look the fifteen classes and their blends join the map palette
  (`maps.map_palette(radar.RAMP)`), so a rain band keeps its colour instead of
  dithering into the woods; the other looks dither the classes into their own
  palettes. A legend panel above the status bar shows the ramp, its mm/h ticks, the
  frame time and how far it is from now, `,` / `.` step through the nowcast from
  -2 h to +2 h, `n` jumps back to now, and the nowcast advances by itself (a new
  frame every five minutes, checked every 20 s). The valid times and the covered
  area come from the server's capabilities (~1 MB, once per start and then every half
  hour, carried on locally in between - the server measured 0.2 to 20 s per answer); each
  picture is fetched for twice the canvas around a centre snapped to half a tile,
  so panning cuts new views out of the same download instead of asking the DWD for
  every key press. Frames are cached under `~/.cache/monocell/radar/` (~30 kB each,
  240 kept) and are then there offline; fetching runs in the background while the
  old picture stays, the map never waits. Outside the DWD's range the panel says
  `NUR UEBER DEUTSCHLAND`. On by `w`, remembered as `map_radar` in the config and
  in Settings -> Maps. Data: (c) Deutscher Wetterdienst, CC BY 4.0.

- **Dithering with a choice.** `d` in the graphics mode used to be an on/off:
  the hand-made 4×4 Bayer matrix or nothing. It now cycles through `BAYER 2`
  (four thresholds, coarse and very visible), `BAYER 4` (sixteen, the classic and
  still the default), `BAYER 8` (sixty-four, almost smooth), `NOISE` (random
  thresholds without a pattern, standing still), `GRAIN` (the same noise, but it
  moves with every frame like film) and `OFF`. For the palette looks the ordered
  ones ride on ffmpeg's `paletteuse` with a scale that follows the matrix size,
  the noise modes add ffmpeg's zero-mean noise to the brightness only (noise in
  all three channels made the palette jump between hues - coloured snow), and the
  grey looks dither with a random threshold mask of their own, which keeps the
  picture's brightness exactly. Measurements on the X61s: 1080p into the
  framebuffer still runs at 31-34 frames a second in every mode, noise costs
  nothing measurable. Settings → Images & video lists the modes too, old configs
  with `bayer` keep working (`bayer4`).
- **Drag and drop between windows.** Press on a file in Files, move two cells and
  let go over another window: the window manager carries a small payload from the
  source app to the one under the pointer. The drag shows the name at the cursor,
  the target window gets an accent frame and the app may narrow it down (Files
  highlights the folder row it would copy into). Files is the source (a marked set
  goes together, an unmarked row goes alone) and a target (copy, with Shift move,
  into the folder under the pointer or the one being shown); the video player
  plays a dropped film, the music player plays it and appends it to its list, the
  picture viewer opens a dropped picture, a terminal types the paths in. No X11,
  no Wayland: the window manager knows where the pointer is and which window it is
  over, so this works on a plain console (the VT mouse reports motion, a terminal
  needs `1003` mouse tracking, which Monocell already switches on).
- **A playlist in the video player.** Dropped films queue up; `l` shows and hides
  the column (`PLAYLIST 2`, current entry marked), `n`/`b` jump to the next or
  previous film, and when one ends the next starts by itself. A drop with nothing
  playing starts at once.

- **Two grey looks in the graphics mode.** `B/W 2` keeps nothing but black and
  white: every shade comes out of the 4×4 Bayer dithering alone (a 1-bit picture
  with seventeen apparent steps, like a printed page), `GREY 16` keeps black,
  white and fourteen evenly spaced steps of grey and dithers between them. Both
  work on brightness only - no tint, and unlike the amber and green monitors no
  contrast curve either, so the picture keeps the brightness it measured. `c`
  cycles through them in the video player, the picture viewer, the maps and the
  browser; Settings → Images & video lists them as well.

- **Console games on Fedora/Nobara.** The Games menu only looked for a binary at
  its Debian path (`/usr/games/...`), so on the Yoga and the workstation every
  entry said "not installed" although the menu was there. A command is now tried
  as given, then on `PATH`, then in `/usr/local/games`, `/usr/games`, `/usr/bin`
  and `/usr/local/bin`; the Freedoom IWAD is looked up in
  `/usr/share/games/doom`, `/usr/share/doom` and `/usr/share/freedoom`. `deploy/nobara/install-games.sh`
  installs what Fedora ships (crawl, cataclysm-dda, nethack, nudoku, moon-buggy,
  chocolate-doom, openarena, freedoom) and copies the small console games only
  Debian has (vitetris, freesweep, ttysolitaire, pacman4console, ninvaders,
  Brogue, text DOOM) from a directory of binaries into `/usr/local/games`.
- **Paint** (Media): ASCII art with pen, line, rectangle, oval, fill, eraser,
  text and a colour picker; undo/redo, three brush modes (`char`; `ramp` takes the
  density from the pen pressure; `smart` takes `- | / \` from the direction), 16
  text and 16 background colours plus "no background". Documents are `.mcpaint`
  JSON and export as `.txt` or `.ans`. On the console Paint reads the pen and the
  touchscreen of a convertible straight from evdev while it has the focus (the
  devices are grabbed, so mousedev does not turn the absolute touch into a
  wandering pointer): pressure is ink density and brush size, the eraser end
  erases. In a terminal the mouse works as usual.

- **Space bar fixed in Snake, Jump and Pi Benchmark.** On a normal console the
  space key arrives as `' '`, and only the kitty keyboard protocol sends
  `ESC[32;1u` (which the VT parser turns into `'space'`). Those apps only knew the
  second name, so "SPACE / ENTER START" did nothing until Enter was pressed.
  Both names are accepted now; JezzBall is written that way from the start.
- **JezzBall (Games).** The field is split by walls while the balls keep bouncing;
  every enclosed area without a ball in it is filled and counts as captured, and
  75 % clears the level, after which the next one starts with one more ball and a
  little more speed. A wall grows from the aim cell outwards (the whole row or
  column, until it hits something) and a ball that touches it while it grows
  destroys it and costs one of the three lives. Arrows/WASD aim, `Space` or a left
  click builds horizontally, `Enter` or a right click vertically; the crosshair
  already shows what each of the two would span. Walls need one second for the
  whole field whatever the window size - on a wide terminal a fixed speed made
  them unplayable. Rules in `monocell/apps/jezzball.py` are terminal free
  (`tests/test_jezzball.py`), the level is redrawn every 30 ms with a real-time
  delta so a slow frame does not stretch the build.
- **Famagic** app: shared family task and shopping lists through the separate
  [famagic-sync](https://github.com/3p1cball-code/famagic-sync) bridge on VEGA. List
  tabs, person filter (ignored for private lists), due dates with a calendar, series
  with "done until the next date", a collapsible done section, shopping lists, private
  lists without assignees and an offline-capable sync (VEGA queues writes when the
  internet is down). The Famagic word in the desktop widget is grey; "Serie" in the
  list is blue.
- **Famagic desktop widget**: bottom-right task box with time range, person and
  per-list switches (Settings → Famagic), wrapping titles, 45 s refresh and a
  click-to-open. Online is green, "VEGA reachable but no internet" orange, VEGA
  unreachable red.
- **Settings**: the overloaded Desktop page is split into Windows, Desktop,
  Background, Famagic and System.
- **YouTube: personal recommendations.** With a sign-in configured, the app opens
  with the account's `youtube.com/feed/recommended` list (at most 25 normal
  videos; Shorts and mixes filtered out) and caches it for 15 minutes. `F3` or
  the "For you" button reloads. Sign-in takes the cookies of Firefox, Brave,
  Chromium or Chrome on the same machine, or an exported Netscape `cookies.txt`;
  Settings -> YouTube explains both. No password reaches the app, and the
  recommendations fall back to search when no sign-in is set.
- The desktop now shows the X61s lid lamp: while the ThinkLight is on, a small
  ASCII cone hangs at the top edge, 4 cm from the left minus three cells (the
  panel's EDID width and the console font turn that into cells: 24 columns in
  Terminus 6x12, 30 in minimal-cell 5x7, 15 in IBM 3270). Amber bulb, bright rays,
  dim tips; the LED is polled every 250 ms and only a change repaints, spaces
  between the rays leave the wallpaper alone. Settings -> Desktop turns the cone
  on or off and picks its size (tiny, mini, small, medium, large); every size hangs from the
  same lamp, so only the beam grows or shrinks.
  `MONOCELL_FAKE_THINKLIGHT` and `MONOCELL_FAKE_PANEL_CM` point the check and the
  panel size at test values for tests and snapshots.
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
