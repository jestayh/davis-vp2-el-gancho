"""Minimal stand-in for MicroPython's framebuf: each character lights its 8x8 cell except column 7"""

MONO_HLSB = 0


class FrameBuffer:
    def __init__(self, buf, w, h, fmt):
        self.w, self.h, self.on = w, h, set()

    def text(self, s, x, y, c):
        for i in range(len(s)):
            for yy in range(8):
                for xx in range(7):
                    self.on.add((x + 8 * i + xx, y + yy))

    def pixel(self, x, y):
        return 1 if (x, y) in self.on else 0
