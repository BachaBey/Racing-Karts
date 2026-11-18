"""Pygame UI toolkit with an arcade-racing look: animated sky + road backdrop, chunky outlined
titles, glossy candy buttons, white panels with coloured header ribbons."""
import math

import pygame

# ---- colours used by the race HUD (on top of the 3D view) -------------------
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

# ---- menu palette ------------------------------------------------------------
INK = (28, 32, 66)
INK_SOFT = (88, 94, 132)
OUTLINE = (24, 26, 52)
PANEL_FILL = (255, 252, 244)
WHITE = (255, 255, 255)
RED = (232, 52, 52)
BLUE = (40, 128, 236)
YELLOW = (255, 204, 40)
GREEN = (60, 186, 84)
ORANGE = (255, 140, 26)
GREY = (150, 152, 172)
DARK_GREEN = (24, 128, 56)
DARK_RED = (190, 44, 54)

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = pygame.font.SysFont("segoeui,verdana,arial", size, bold=bold)
    return _fonts[key]


def display_font(size):
    key = ("display", size)
    if key not in _fonts:
        _fonts[key] = pygame.font.SysFont("arialblack,impact,segoeuiblack,verdana", size)
    return _fonts[key]


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


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


# ---- chunky outlined text (titles, buttons) ------------------------------------
_otext_cache = {}


def _vgrad(size, top, bottom):
    w, h = size
    g = pygame.Surface((w, h))
    for y in range(h):
        pygame.draw.line(g, _mix(top, bottom, y / max(1, h - 1)), (0, y), (w, y))
    return g


def outlined(s, size, fill=WHITE, outline=OUTLINE, thick=3, grad=None, depth=None):
    """Text with a thick dark outline and a 3D drop below it (cached)."""
    key = (s, size, fill, outline, thick, grad, depth)
    out = _otext_cache.get(key)
    if out is not None:
        return out
    f = display_font(size)
    base = f.render(str(s), True, WHITE)
    edge = f.render(str(s), True, outline)
    w, h = base.get_size()
    depth = thick + 1 if depth is None else depth
    out = pygame.Surface((w + thick * 2 + 2, h + thick * 2 + depth + 2), pygame.SRCALPHA)
    for d in range(depth + 1):                          # 3D extrusion below
        for i in range(16):
            a = i * math.pi / 8
            out.blit(edge, (thick + round(math.cos(a) * thick), thick + round(math.sin(a) * thick) + d))
    if grad:
        colored = base.copy()
        colored.blit(_vgrad((w, h), grad[0], grad[1]), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    else:
        colored = f.render(str(s), True, fill)
    out.blit(colored, (thick, thick))
    if len(_otext_cache) > 600:
        _otext_cache.clear()
    _otext_cache[key] = out
    return out


def otext(surf, s, pos, size, fill=WHITE, outline=OUTLINE, thick=3, grad=None, center=False, right=False):
    img = outlined(s, size, fill, outline, thick, grad)
    r = img.get_rect()
    if center:
        r.center = pos
    elif right:
        r.topright = pos
    else:
        r.topleft = pos
    surf.blit(img, r)
    return r


LOGO_GRAD = ((255, 236, 90), (255, 128, 20))


def logo(surf, center, size=60, words="TURBO KART ARENA"):
    """Bouncing letters, yellow-to-orange gradient, thick outline."""
    t = pygame.time.get_ticks() / 1000.0
    letters = [outlined(ch, size, grad=LOGO_GRAD, thick=5) if ch != " " else None for ch in words]
    space = size // 3
    widths = [(img.get_width() - 8) if img else space for img in letters]
    total = sum(widths)
    x = center[0] - total / 2
    for i, img in enumerate(letters):
        if img is not None:
            bob = math.sin(t * 3.2 + i * 0.45) * size * 0.06
            surf.blit(img, img.get_rect(midtop=(int(x + widths[i] / 2), int(center[1] - img.get_height() / 2 + bob))))
        x += widths[i]


def ribbon(surf, center, s, color=BLUE, size=18):
    """Small pill banner with outlined white text."""
    img = outlined(s, size, thick=2, depth=1)
    r = pygame.Rect(0, 0, img.get_width() + 36, img.get_height() + 6)
    r.center = center
    pygame.draw.rect(surf, OUTLINE, r.move(0, 4), border_radius=r.h // 2)
    pygame.draw.rect(surf, color, r, border_radius=r.h // 2)
    pygame.draw.rect(surf, OUTLINE, r, 3, border_radius=r.h // 2)
    surf.blit(img, img.get_rect(center=(r.centerx, r.centery - 1)))
    return r


# ---- animated menu backdrop -------------------------------------------------
_bg = {}
_menu_sprites = None


def _sprites():
    global _menu_sprites
    if _menu_sprites is None:
        from client.render.sprites import SpriteBank   # lazy: sprites.py imports this module
        _menu_sprites = SpriteBank()
        _menu_sprites.prewarm([0, 1, 3, 5])
    return _menu_sprites


def _build_bg(size):
    import random
    W, H = size
    rng = random.Random(7)
    horizon = int(H * 0.62)
    s = _vgrad((W, H), (52, 140, 240), (176, 222, 255))
    for layer, (col, amp, base) in enumerate((((130, 196, 170), 70, horizon - 20), ((84, 172, 92), 44, horizon + 6))):
        pts = [(0, H)]
        y = base
        vel = 0
        for x in range(0, W + 20, 20):
            vel = vel * 0.8 + rng.uniform(-6, 6)
            y = max(base - amp, min(base + 10, y + vel))
            pts.append((x, y))
        pts.append((W, H))
        pygame.draw.polygon(s, col, pts)
        if layer == 1:   # dotted bushes on the near hills
            for x in range(10, W, 46):
                yy = max(p[1] for p in pts if abs(p[0] - x) < 20) + rng.randint(4, 14)
                pygame.draw.circle(s, (60, 146, 70), (x, int(yy)), rng.randint(9, 15))
    grass_top = horizon + 34
    pygame.draw.rect(s, (92, 182, 74), (0, grass_top, W, H - grass_top))
    for x in range(0, W, 96):                          # mowed stripes
        pygame.draw.rect(s, (84, 170, 68), (x, grass_top, 48, H - grass_top))
    road_top = H - 112
    for x in range(0, W, 24):                          # kerb
        pygame.draw.rect(s, (230, 40, 40) if (x // 24) % 2 else (245, 245, 245), (x, road_top - 12, 24, 12))
    pygame.draw.rect(s, (72, 74, 86), (0, road_top, W, H - road_top))
    pygame.draw.rect(s, (60, 62, 72), (0, road_top + 36, W, 30))
    clouds = []
    for _ in range(6):
        cw = rng.randint(180, 300)
        c = pygame.Surface((cw, 90), pygame.SRCALPHA)
        for _k in range(7):
            r = rng.randint(22, 36)
            pygame.draw.circle(c, (255, 255, 255, 235), (rng.randint(r, cw - r), rng.randint(44, 60)), r)
        pygame.draw.rect(c, (255, 255, 255, 235), (24, 58, cw - 48, 26), border_radius=13)
        clouds.append((c, rng.uniform(0, W), rng.uniform(20, horizon - 160), rng.uniform(6, 16)))
    return {"static": s.convert() if pygame.display.get_surface() else s, "clouds": clouds, "road_top": road_top}


def background(surf):
    size = surf.get_size()
    if size not in _bg:
        _bg[size] = _build_bg(size)
    bg = _bg[size]
    W, H = size
    t = pygame.time.get_ticks() / 1000.0
    surf.blit(bg["static"], (0, 0))
    for c, x0, y, speed in bg["clouds"]:
        x = (x0 + t * speed) % (W + c.get_width()) - c.get_width()
        surf.blit(c, (int(x), int(y)))
    road_top = bg["road_top"]
    off = int(t * 420) % 90
    for x in range(-90, W + 90, 90):                   # scrolling centre dashes
        pygame.draw.rect(surf, (240, 240, 240), (x + off, road_top + 48, 46, 7))
    sp = _sprites()
    frame_r = math.pi / 2                               # side view, driving to the right
    for i, (col, speed, lane, phase) in enumerate(((0, 180, 6, 0.0), (1, 150, 40, 0.45), (3, 210, 22, 0.7), (5, 130, 52, 0.2))):
        img = sp.kart(col, frame_r)
        big = sp.scaled(img, 150, ("menukart", col))
        span = W + 300
        x = (phase * span + t * speed) % span - 200
        bob = abs(math.sin(t * 14 + i)) * 2
        surf.blit(big, big.get_rect(midbottom=(int(x), int(road_top + lane + 58 - bob))))
    for i, (bx, by) in enumerate(((70, 120), (1210, 96))):   # floating item boxes
        frame = sp.box_frames[int(t * 8 + i * 3) % len(sp.box_frames)]
        box = sp.scaled(frame, 76, ("menubox", id(frame)))
        surf.blit(box, box.get_rect(center=(bx, int(by + math.sin(t * 2 + i) * 8))))


# ---- panels -----------------------------------------------------------------
_panel_cache = {}


def panel(surf, rect, title=None, color=BLUE, dark=False):
    rect = pygame.Rect(rect)
    if dark:   # used over the race view (results)
        s = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(s, (*PANEL, 235), s.get_rect(), border_radius=16)
        pygame.draw.rect(s, PANEL_EDGE, s.get_rect(), 3, border_radius=16)
        surf.blit(s, rect.topleft)
        if title:
            text(surf, title, (rect.x + 16, rect.y + 10), 20, ACCENT, bold=True)
        return
    key = (rect.size, title, color)
    img = _panel_cache.get(key)
    if img is None:
        w, h = rect.size
        img = pygame.Surface((w, h + 10), pygame.SRCALPHA)
        pygame.draw.rect(img, (0, 0, 0, 70), (0, 10, w, h), border_radius=22)
        pygame.draw.rect(img, PANEL_FILL, (0, 0, w, h), border_radius=22)
        if title:
            pygame.draw.rect(img, color, (0, 0, w, 54), border_top_left_radius=22, border_top_right_radius=22)
            pygame.draw.rect(img, _shade(color, 1.18), (10, 6, w - 20, 12), border_radius=6)
            pygame.draw.line(img, OUTLINE, (0, 54), (w, 54), 3)
            t = outlined(title, 24, thick=3)
            img.blit(t, (20, 27 - t.get_height() // 2))
        pygame.draw.rect(img, OUTLINE, (0, 0, w, h), 4, border_radius=22)
        _panel_cache[key] = img
    surf.blit(img, rect.topleft)


# ---- widgets ----------------------------------------------------------------
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


_btn_cache = {}


def _glossy(size, color, radius):
    key = (size, color, radius)
    img = _btn_cache.get(key)
    if img is None:
        w, h = size
        img = pygame.Surface(size, pygame.SRCALPHA)
        img.blit(_vgrad(size, _shade(color, 1.28), _shade(color, 0.9)), (0, 0))
        gloss = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.rect(gloss, (255, 255, 255, 70), (8, 4, w - 16, h // 2 - 4), border_radius=radius)
        img.blit(gloss, (0, 0))
        mask = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), border_radius=radius)
        img.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        _btn_cache[key] = img
    return img


class Button(Widget):
    def __init__(self, rect, label, on_click, color=RED, size=22):
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
        base = self.color if self.enabled else GREY
        if self.hover and self.enabled:
            base = _shade(base, 1.12)
        lift = -3 if (self.hover and self.enabled) else 0
        r = self.rect.move(0, lift)
        rad = min(22, r.h // 2)
        pygame.draw.rect(surf, OUTLINE, self.rect.move(0, 7), border_radius=rad)
        pygame.draw.rect(surf, _shade(base, 0.62), r.move(0, 5), border_radius=rad)
        surf.blit(_glossy(r.size, base, rad), r.topleft)
        pygame.draw.rect(surf, OUTLINE, r, 3, border_radius=rad)
        img = outlined(self.label, self.size, thick=2, depth=2)
        surf.blit(img, img.get_rect(center=(r.centerx, r.centery)))


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
        bg = WHITE if self.enabled else (232, 232, 240)
        pygame.draw.rect(surf, bg, self.rect, border_radius=12)
        pygame.draw.line(surf, (222, 226, 240), (self.rect.x + 10, self.rect.y + 5), (self.rect.right - 10, self.rect.y + 5), 3)
        if self.focused:
            pygame.draw.rect(surf, BLUE, self.rect.inflate(6, 6), 4, border_radius=15)
        pygame.draw.rect(surf, OUTLINE, self.rect, 3, border_radius=12)
        shown = ("*" * len(self.value)) if self.password else self.value
        if shown:
            r = text(surf, shown, (self.rect.x + 14, self.rect.centery - 14), 22, INK, bold=True)
        else:
            r = text(surf, self.placeholder, (self.rect.x + 14, self.rect.centery - 13), 20, INK_SOFT)
            r.width = 0
        if self.focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = self.rect.x + 16 + (r.width if shown else 0)
            pygame.draw.line(surf, INK, (x, self.rect.y + 10), (x, self.rect.bottom - 10), 3)


class Cycle(Widget):
    """Label + value cycling through options (click: next, right-click: previous)."""

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
            back = ev.button == 3 or ev.pos[0] < self.rect.x + self.rect.w * 0.45
            self.index = (self.index + (-1 if back else 1)) % len(self.options)
            if self.on_change:
                self.on_change(self.value)
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, WHITE, self.rect, border_radius=12)
        pygame.draw.rect(surf, OUTLINE, self.rect, 3, border_radius=12)
        text(surf, self.label, (self.rect.x + 14, self.rect.centery - 12), 18, INK_SOFT, bold=True)
        col = BLUE if self.enabled else GREY
        cy = self.rect.centery
        val = font(21, True).render(self.options[self.index][1], True, INK if self.enabled else INK_SOFT)
        vr = val.get_rect(midright=(self.rect.right - 40, cy))
        surf.blit(val, vr)
        lx = vr.x - 22
        pygame.draw.polygon(surf, col, [(lx + 10, cy - 9), (lx + 10, cy + 9), (lx, cy)])
        rx = self.rect.right - 30
        pygame.draw.polygon(surf, col, [(rx, cy - 9), (rx, cy + 9), (rx + 10, cy)])


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
        box = pygame.Rect(self.rect.x, self.rect.centery - 15, 30, 30)
        pygame.draw.rect(surf, GREEN if self.value else WHITE, box, border_radius=8)
        pygame.draw.rect(surf, OUTLINE, box, 3, border_radius=8)
        if self.value:
            pygame.draw.lines(surf, WHITE, False, [(box.x + 6, box.centery), (box.x + 12, box.bottom - 7),
                                                   (box.right - 6, box.y + 7)], 5)
        text(surf, self.label, (box.right + 12, self.rect.centery - 13), 21, INK, bold=True)


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
        pygame.draw.rect(surf, WHITE, self.rect, border_radius=12)
        head = pygame.Rect(self.rect.x, self.rect.y, self.rect.w, self.row_h)
        pygame.draw.rect(surf, (226, 232, 250), head, border_top_left_radius=12, border_top_right_radius=12)
        x = self.rect.x + 14
        size = 17 if self.row_h >= 30 else 15
        for title, w in self.columns:
            text(surf, title.upper(), (x, self.rect.y + (self.row_h - size - 6) // 2), size, INK_SOFT, bold=True)
            x += w
        y = self.rect.y + self.row_h
        for i, (key, cells, color) in enumerate(self.rows):
            if y + self.row_h > self.rect.bottom - 2:
                break
            r = pygame.Rect(self.rect.x + 4, y + 1, self.rect.w - 8, self.row_h - 2)
            if key == self.selected:
                pygame.draw.rect(surf, YELLOW, r, border_radius=8)
                pygame.draw.rect(surf, OUTLINE, r, 2, border_radius=8)
            elif i % 2:
                pygame.draw.rect(surf, (240, 243, 252), r, border_radius=8)
            x = self.rect.x + 14
            fs = 19 if self.row_h >= 30 else 16
            for (title, w), cell in zip(self.columns, cells):
                text(surf, cell, (x, y + (self.row_h - fs - 8) // 2), fs, color or INK, bold=True)
                x += w
            y += self.row_h
        if not self.rows:
            text(surf, "(empty)", (self.rect.centerx, self.rect.y + self.row_h + 24), 18, INK_SOFT, center=True)
        pygame.draw.rect(surf, OUTLINE, self.rect, 3, border_radius=12)


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
            img = outlined(self.message, 20, fill=self.message_color, thick=3, depth=2)
            r = img.get_rect(center=(surf.get_width() // 2, surf.get_height() - 40))
            bg = r.inflate(28, 12)
            s = pygame.Surface(bg.size, pygame.SRCALPHA)
            pygame.draw.rect(s, (20, 22, 44, 190), s.get_rect(), border_radius=bg.h // 2)
            surf.blit(s, bg.topleft)
            surf.blit(img, r)

    def on_message(self, lp, msg):
        pass

    def update(self, dt):
        pass

    def draw(self, surf):
        pass
