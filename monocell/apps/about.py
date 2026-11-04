"""About: why this project exists."""
from .. import __version__, logo
from ..app import App
from ..util import fit


TEXT = [
    'Ein Text-Desktop für das perfekte X61s.',
    '',
    'Alexander Riedel · Berlin · alexander.riedel@eyedea3d.com',
    '',
    'Warum?',
    'Ich wollte das perfekte X61s bauen: IBM-Branding, alle drei Akkus,',
    'Middleton-BIOS, 3270-Schrift, IBM-Theme — und eine Oberfläche, die',
    'zur Hardware passt. Monocell ist ein Text-Desktop im Geist von DOS,',
    'ANSI und BBS: Fenster, Apps, Browser, Mail, Karten, Video — alles',
    'aus 16 Farben und Zeichen. Kein Electron, keine GPU, kein Ballast.',
    'Ein Rechner, der auch in 20 Jahren noch versteht, was er tut.',
    '',
    'Technik: Python, eigener Zellen-Compositor, Linux-VT, 16 Farben.',
    'Hardware: ThinkPad X61s · L7700 · 8 GB · 128 GB SSD · 3270.',
    'Danke an Kristina, Klemens und Kasimir.',
]


class About(App):
    name = 'about'
    title = 'About'
    icon = '[?]'
    size = (78, 26)
    min_size = (40, 12)
    single = True

    def draw(self, cv, focused):
        th = self.th
        width = cv.w
        logo_width, _, _ = logo.measure_for(width, hint=False)
        _, logo_height = logo.draw(cv, max(0, (width - logo_width) // 2), 1,
                                   th, hint=False)
        y = 1 + logo_height + 1
        for line in TEXT:
            if y >= cv.h:
                break
            if line:
                cv.put(0, y, fit(line, width), th.c('text', 'win'))
            y += 1
        cv.put(0, cv.h - 1, fit('Monocell %s' % __version__, width),
               th.c('dim', 'win'))


APP = About
