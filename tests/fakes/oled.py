"""Pixel-accurate stand-in for the SSD1306 driver, recording every frame sent with show()"""


class FakeOled:
    def __init__(self, strict=True):
        self.px = [[0] * 128 for _ in range(64)]
        self.texts = []
        self.frames = []
        self.cmds = []
        self.strict = strict   # assert that shapes stay on screen (framebuf itself clips silently)

    def _set(self, x, y, c):
        if 0 <= x < 128 and 0 <= y < 64:
            self.px[y][x] = c
        elif self.strict:
            raise AssertionError("pixel off screen: ({}, {})".format(x, y))

    # framebuf primitives used by src/display.py
    def fill(self, c):
        self.px = [[c] * 128 for _ in range(64)]
        self.texts = []

    def pixel(self, x, y, c):
        self._set(x, y, c)

    def hline(self, x, y, w, c):
        for i in range(w):
            self._set(x + i, y, c)

    def vline(self, x, y, h, c):
        for i in range(h):
            self._set(x, y + i, c)

    def fill_rect(self, x, y, w, h, c):
        for yy in range(h):
            self.hline(x, y + yy, w, c)

    def rect(self, x, y, w, h, c):
        self.hline(x, y, w, c)
        self.hline(x, y + h - 1, w, c)
        self.vline(x, y, h, c)
        self.vline(x + w - 1, y, h, c)

    def line(self, x0, y0, x1, y1, c):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            self._set(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n, c)

    def text(self, s, x, y, c):
        self.texts.append((s, x, y, c))
        for i in range(len(s)):
            for yy in range(7):
                for xx in range(6):
                    xp = x + 8 * i + xx
                    if 0 <= xp < 128:          # text is clipped like the real framebuf
                        self.px[y + yy][xp] = c

    def show(self):
        self.frames.append(([row[:] for row in self.px], list(self.texts)))

    # SSD1306 commands
    def poweroff(self):
        self.cmds.append("off")

    def poweron(self):
        self.cmds.append("on")

    def contrast(self, c):
        self.cmds.append(("contrast", c))

    # helpers for assertions
    def last_texts(self):
        return self.frames[-1][1]

    def header(self):
        """Texts of the header band (y=4) in the last frame"""
        return [t for t in self.last_texts() if t[2] == 4]

    def lit(self, y0, y1, x0=0, x1=128, frame=-1):
        px = self.frames[frame][0]
        return sum(px[y][x] for y in range(y0, y1) for x in range(x0, x1))
