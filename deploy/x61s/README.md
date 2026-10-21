# monocell auf dem X61s (Debian, Kernel-VT)

Ziel: monocell als **eigenes Frontend auf einem Virtual Terminal** – ohne X/Wayland,
ohne Desktop-Session. Das vorhandene minimale UI bleibt installiert und ist über
`Ctrl+Alt+F1` weiter erreichbar; monocell läuft standardmäßig auf `tty2`.

## Voraussetzungen

- Debian 13 auf dem X61s (x86-64-v1), Kernel-Konsole mit `TERM=linux`
- Python ≥ 3.9, ein paar Pakete (das Skript installiert sie):
  `python3`, `python3-pil`, `ffmpeg`, `poppler-utils`, `yt-dlp` (das Skript lädt zusätzlich
  die aktuelle yt-dlp-Binary nach `/usr/local/bin`, weil die Debian-Version zu alt ist)
- Maus auf dem VT: Benutzer in der Gruppe `input` (`/dev/input/mice`)
- **`gpm` muss aus sein** (es zeichnet sonst einen zweiten Mauszeiger); das Installationsskript
  deaktiviert `gpm.service` automatisch.

## Installation (auf dem X61s)

```sh
# Repo auf den X61s bringen (rsync/scp), dann:
sudo ~/monocell/deploy/x61s/install-vt.sh --user ariedel
sudo ~/monocell/deploy/x61s/install-login.sh --user ariedel
```

## Umstieg von der alten `textwin`-Installation

Läuft auf dem Gerät noch die frühere Version, übernimmt dieses Skript den Umbau:
alte `textwin`-Units, Launcher, `/opt/textwin`, Polkit-Regel, GRUB-Eintrag und den
Autostart-Block werden entfernt, danach werden die monocell-Systemdateien normal
installiert.

```sh
sudo ~/monocell-src/deploy/x61s/upgrade-from-textwin.sh --user ariedel --kdeconnect
```

Danach neu starten und im GRUB-Menü **„MONOCELL - TUI“** wählen (oder den
normalen Eintrag nehmen). `--keep-old` behält `/opt/textwin`, `--no-grub` lässt den
GRUB-Teil aus.

`install-vt.sh` installiert Code und Launcher (standardmäßig **kein** loginloser Dienst),
`install-login.sh` richtet den Text-Login ein:

- `getty` auf tty1, tty2 und tty3.
- Nach dem Login auf **tty1** startet monocell automatisch (`~/.profile`, Marker-Block);
  **tty2/tty3** sind normale Shell-Logins (Rettungsweg). Beenden von monocell = Logout.
- **Fingerprint** über `pam_fprintd` (der X61s-Reader ist eingelernt); Passwort bleibt
  Fallback. Fehlt ein Fingerabdruck: `fprintd-enroll ariedel`.
- **Reboot/Shut down** im Startmenü; eine Polkit-Regel erlaubt diese Aktionen für den
  Benutzer.
- **Tailscale** wird als Operator für den Benutzer eingerichtet; die App „Tailscale“
  (Tools) zeigt Status/Peers und kann verbinden/trennen.
- `gpm` wird deaktiviert (zweiter Mauszeiger).

Loginloser Kiosk (kein Text-Login, nur für spezielle Fälle):
`install-vt.sh --user ariedel --kiosk` (startet monocell@tty2 ohne Anmeldung).

## Ohne Desktop booten (extra wenig Speicher)

```sh
sudo ~/monocell/deploy/x61s/install-vt.sh --user ariedel --no-desktop
```

Das setzt `multi-user.target` als Standard und deaktiviert den Displaymanager
(lightdm/gdm/sddm). Das Skript zeigt den Rollback-Befehl an. Reiner Textbetrieb
braucht ~25 MB RSS im Leerlauf (gemessen mit `tools/memcheck.py`), gegenüber
einer grafischen Sitzung mit mehreren hundert MB.

`monocell-session` startet mit `--lowmem`: nur Performance-Regler (keine Schatten,
Video mit 10 fps). Aussehen, Bildmodus und Web-Engine folgen deinen Einstellungen.

## Alternativer GRUB-Eintrag (direkt in monocell booten)

```sh
sudo ~/monocell/deploy/x61s/install-grub-entry.sh
```

Das Skript legt `monocell.target` an (Text-Login auf tty1; monocell startet nach der
Anmeldung), klont den GRUB-Eintrag des **laufenden Kernels** und hängt
`systemd.unit=monocell.target` an, macht das GRUB-Menü sichtbar (Timeout 5 s, Backup
unter `/etc/default/grub.bak-monocell`) und ruft `update-grub` auf. Der normale grafische
Eintrag bleibt Standard – im GRUB-Menü **„MONOCELL - TUI“** wählen, um in die
Text-Anmeldung zu booten.

Nach einem Kernel-Update das Skript erneut ausführen (der Eintrag zeigt auf den beim
Installieren laufenden Kernel). Entfernen mit `... --remove`.

## Deploy vom Arbeitsrechner

```sh
./deploy/x61s/deploy-from-workstation.sh ariedel@x61s
# optional: ... ariedel@x61s --no-desktop
```

Das kopiert den Baum nach `/tmp/monocell-src` und ruft den Installer per SSH auf.
Der Host muss per SSH-Key erreichbar sein (`~/.ssh/config`).

Der Dienst setzt `TERM=linux` (Palette per `ESC ] P`, Maus über `/dev/input/mice`).
Wer monocell von Hand auf einem VT startet, sollte das ebenfalls tun:
`TERM=linux monocell`.

## Nach der Installation prüfen

```sh
# Terminal-Größe: bei 1024x768 und 8x16-Zellen sind es 128x48
tput cols; tput lines

# Maus:
ls -l /dev/input/mice        # Gruppe input, lesbar
id -nG | grep -w input

# Speicher:
tools/memcheck.py            # normal / --lowmem

# Bild/PDF/Ton:
monocell --open pdf /usr/share/doc/...pdf
```

## Deinstallation

```sh
sudo ~/monocell/deploy/x61s/install-vt.sh --uninstall --tty 2
sudo systemctl set-default graphical.target   # falls --no-desktop genutzt wurde
```

## KDE Connect und Zwischenablage

Auf der Konsole gibt es keine System-Zwischenablage. Für den Austausch mit dem Handy
startet `install-kdeconnect.sh` ein headless Xvfb (`:99`) und den KDE-Connect-Daemon:

```sh
sudo ~/monocell/deploy/x61s/install-kdeconnect.sh --user ariedel
```

Danach in Monocell **System → KDE Connect** öffnen, mit `P` pairen (am Handy bestätigen)
und dort das Clipboard-Plugin aktivieren. `V` holt die Handy-Zwischenablage nach
Monocell, `S` schickt die Monocell-Zwischenablage ans Handy. Neu empfangene Dateien aus
`~/Downloads` lassen sich mit `N` direkt in die Zwischenablage laden.

In jeder Shell (auch tty2/tty3) liest und schreibt `monocell-clip` dieselbe Ablage:

```sh
monocell-clip                 # ausgeben
monocell-clip --set < datei   # setzen
```

## Grenzen / Hinweise

- Auf dem VT gibt es nur 8 Hintergrundfarben; monocell sortiert die Palette
  entsprechend. Symbole werden dort automatisch zu ASCII (Einstellungen → Desktop →
  Symbols: auto/on/off).
- Für UTF-8-Eingabe (Umlaute) auf der Konsole: `sudo kbd_mode -u` (der Dienst
  versucht das beim Start; Debian ist normalerweise bereits UTF-8).
- Chromium/Brave ist auf dem X61s optional; ohne Browser läuft die lite-Engine.
- Auf dem VT ist der Software-Zeiger von monocell aktiv (kein gpm nötig).
