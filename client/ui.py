"""Minimal pygame UI toolkit: buttons, text inputs, cycles, toggles, list views."""
import pygame

BG_TOP = (18, 22, 48)
BG_BOT = (48, 20, 60)
PANEL = (30, 34, 66)
PANEL_EDGE = (80, 90, 150)
TEXT = (235, 238, 255)
MUTED = (150, 158, 200)
ACCENT = (255, 170, 40)
ACCENT2 = (60, 200, 255)
GOOD = (90, 220, 120)
BAD = (255, 90, 90)
PLAYER_COLORS = [(230, 50, 50), (50, 110, 240), (50, 200, 80), (250, 210, 40),
                 (170, 70, 230), (255, 130, 30), (220, 220, 230), (120, 230, 230), (240, 120, 200)]

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = pygame.font.SysFont("segoeui,verdana,arial", size, bold=bold)
    return _fonts[key]


def text(surf, s, pos, size=20, color=TEXT, bold=False, center=False, right=False, shadow=False):
    img = font(size, bold).render(str(s), True, color)
    r = img.get_rect()
    if center:
        r.center = pos
    elif right:
        r.topright = pos
    else:
        r.topleft = pos
    if shadow:
        sh = font(size, bold).render(str(s), True, (0, 0, 0))
        surf.blit(sh, r.move(2, 2))
    surf.blit(img, r)
    return r


_bg_cache = {}


def background(surf):
    size = surf.get_size()
    if size not in _bg_cache:
        bg = pygame.Surface(size)
        w, h = size
        for y in range(h):
            f = y / h
            c = [int(BG_TOP[i] * (1 - f) + BG_BOT[i] * f) for i in range(3)]
            pygame.draw.line(bg, c, (0, y), (w, y))
        stripes = pygame.Surface(size, pygame.SRCALPHA)
        for i in range(0, w + h, 46):   # subtle racing stripes
            pygame.draw.line(stripes, (255, 255, 255, 12), (i, 0), (i - h, h), 2)
        bg.blit(stripes, (0, 0))
        _bg_cache[size] = bg
    surf.blit(_bg_cache[size], (0, 0))


def panel(surf, rect, title=None):
    s = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(s, (*PANEL, 225), s.get_rect(), border_radius=12)
    pygame.draw.rect(s, PANEL_EDGE, s.get_rect(), 2, border_radius=12)
    surf.blit(s, rect.topleft)
    if title:
        text(surf, title, (rect.x + 16, rect.y + 10), 20, ACCENT, bold=True)


class Widget:
    def __init__(self, rect):
        self.rect = pygame.Rect(rect)
        self.visible = True
        self.enabled = True
        self.focusable = False
        self.focused = False

    def handle(self, ev):
        return False

    def draw(self, surf):
        pass


class Button(Widget):
    def __init__(self, rect, label, on_click, color=ACCENT, size=22):
        super().__init__(rect)
        self.label = label
        self.on_click = on_click
        self.color = color
        self.size = size
        self.hover = False

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            self.on_click()
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        c = self.color if self.enabled else (90, 90, 110)
        if self.hover and self.enabled:
            c = tuple(min(255, v + 35) for v in c)
        sh = self.rect.move(0, 4)
        pygame.draw.rect(surf, tuple(max(0, v - 90) for v in c), sh, border_radius=10)
        pygame.draw.rect(surf, c, self.rect, border_radius=10)
        tc = (25, 20, 35) if sum(c) > 380 else TEXT
        text(surf, self.label, self.rect.center, self.size, tc, bold=True, center=True)


class TextInput(Widget):
    def __init__(self, rect, placeholder="", password=False, max_len=24, value=""):
        super().__init__(rect)
        self.value = value
        self.placeholder = placeholder
        self.password = password
        self.max_len = max_len
        self.focusable = True
        self.on_enter = None

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            hit = self.rect.collidepoint(ev.pos)
            self.focused = hit
            return hit
        if self.focused and ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_BACKSPACE:
                self.value = self.value[:-1]
            elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.on_enter:
                    self.on_enter()
            elif ev.key == pygame.K_v and ev.mod & pygame.KMOD_CTRL:
                try:
                    clip = pygame.scrap.get_text() if hasattr(pygame.scrap, "get_text") else ""
                    self.value = (self.value + (clip or "").strip())[: self.max_len]
                except Exception:
                    pass
            elif ev.unicode and ev.unicode.isprintable() and ev.key != pygame.K_TAB:
                if len(self.value) < self.max_len:
                    self.value += ev.unicode
            else:
                return False
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (14, 16, 34), self.rect, border_radius=8)
        pygame.draw.rect(surf, ACCENT2 if self.focused else PANEL_EDGE, self.rect, 2, border_radius=8)
        shown = ("*" * len(self.value)) if self.password else self.value
        if shown:
            r = text(surf, shown, (self.rect.x + 12, self.rect.centery - 13), 22)
        else:
            r = text(surf, self.placeholder, (self.rect.x + 12, self.rect.centery - 13), 22, MUTED)
            r.width = 0
        if self.focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = self.rect.x + 14 + (r.width if shown else 0)
            pygame.draw.line(surf, TEXT, (x, self.rect.y + 9), (x, self.rect.bottom - 9), 2)


class Cycle(Widget):
    """Label + clickable value cycling through options."""

    def __init__(self, rect, label, options, index=0, on_change=None):
        super().__init__(rect)
        self.label = label
        self.options = options      # list of (value, display)
        self.index = index
        self.on_change = on_change

    @property
    def value(self):
        return self.options[self.index][0]

    def set_value(self, v):
        for i, (val, _d) in enumerate(self.options):
            if val == v:
                self.index = i

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button in (1, 3) and self.rect.collidepoint(ev.pos):
            self.index = (self.index + (1 if ev.button == 1 else -1)) % len(self.options)
            if self.on_change:
                self.on_change(self.value)
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (14, 16, 34), self.rect, border_radius=8)
        pygame.draw.rect(surf, PANEL_EDGE if self.enabled else (60, 60, 80), self.rect, 2, border_radius=8)
        text(surf, self.label, (self.rect.x + 12, self.rect.centery - 11), 18, MUTED)
        col = ACCENT if self.enabled else MUTED
        text(surf, "< " + self.options[self.index][1] + " >", (self.rect.right - 12, self.rect.centery - 12),
             20, col, bold=True, right=True)


class Toggle(Widget):
    def __init__(self, rect, label, value=False, on_change=None):
        super().__init__(rect)
        self.label = label
        self.value = value
        self.on_change = on_change

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            self.value = not self.value
            if self.on_change:
                self.on_change(self.value)
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        box = pygame.Rect(self.rect.x, self.rect.centery - 13, 26, 26)
        pygame.draw.rect(surf, (14, 16, 34), box, border_radius=6)
        pygame.draw.rect(surf, ACCENT2, box, 2, border_radius=6)
        if self.value:
            pygame.draw.lines(surf, GOOD, False, [(box.x + 5, box.centery), (box.x + 11, box.bottom - 6),
                                                  (box.right - 5, box.y + 6)], 4)
        text(surf, self.label, (box.right + 10, self.rect.centery - 12), 20)


class ListView(Widget):
    def __init__(self, rect, columns, on_select=None, on_activate=None, row_h=34):
        super().__init__(rect)
        self.columns = columns      # list of (title, width)
        self.rows = []              # list of (key, [cells], color)
        self.selected = None
        self.on_select = on_select
        self.on_activate = on_activate
        self.row_h = row_h
        self._last_click = 0

    def set_rows(self, rows):
        self.rows = rows
        if self.selected not in [r[0] for r in rows]:
            self.selected = None

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            idx = (ev.pos[1] - self.rect.y - self.row_h) // self.row_h
            if 0 <= idx < len(self.rows):
                key = self.rows[idx][0]
                now = pygame.time.get_ticks()
                double = key == self.selected and now - self._last_click < 400
                self.selected = key
                self._last_click = now
                if self.on_select:
                    self.on_select(key)
                if double and self.on_activate:
                    self.on_activate(key)
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (14, 16, 34), self.rect, border_radius=8)
        pygame.draw.rect(surf, PANEL_EDGE, self.rect, 2, border_radius=8)
        x = self.rect.x + 12
        for title, w in self.columns:
            text(surf, title, (x, self.rect.y + 6), 16, MUTED, bold=True)
            x += w
        y = self.rect.y + self.row_h
        for key, cells, color in self.rows:
            if y + self.row_h > self.rect.bottom:
                break
            r = pygame.Rect(self.rect.x + 4, y, self.rect.w - 8, self.row_h - 2)
            if key == self.selected:
                pygame.draw.rect(surf, (70, 60, 130), r, border_radius=6)
            x = self.rect.x + 12
            for (title, w), cell in zip(self.columns, cells):
                text(surf, cell, (x, y + 5), 19, color or TEXT)
                x += w
            y += self.row_h
        if not self.rows:
            text(surf, "(empty)", (self.rect.centerx, self.rect.y + self.row_h + 20), 18, MUTED, center=True)


class Scene:
    """Base scene: owns widgets, handles focus (Tab) and shows status messages."""

    def __init__(self, app):
        self.app = app
        self.widgets = []
        self.message = ""
        self.message_color = MUTED
        self.message_until = 0

    def add(self, w):
        self.widgets.append(w)
        return w

    def flash(self, msg, color=BAD, secs=4.0):
        self.message = msg
        self.message_color = color
        self.message_until = pygame.time.get_ticks() + int(secs * 1000)

    def handle_event(self, ev):
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_TAB:
            fields = [w for w in self.widgets if w.focusable and w.visible and w.enabled]
            if fields:
                cur = next((i for i, w in enumerate(fields) if w.focused), -1)
                for w in fields:
                    w.focused = False
                step = -1 if ev.mod & pygame.KMOD_SHIFT else 1
                fields[(cur + step) % len(fields)].focused = True
            return True
        for w in self.widgets:
            if w.handle(ev):
                return True
        if ev.type == pygame.MOUSEBUTTONDOWN:
            for w in self.widgets:
                if w.focusable:
                    w.focused = False
        return False

    def draw_widgets(self, surf):
        for w in self.widgets:
            w.draw(surf)
        if self.message and pygame.time.get_ticks() < self.message_until:
            r = text(surf, self.message, (surf.get_width() // 2, surf.get_height() - 40), 22,
                     self.message_color, bold=True, center=True, shadow=True)
            del r

    def on_message(self, lp, msg):
        pass

    def update(self, dt):
        pass

    def draw(self, surf):
        pass
