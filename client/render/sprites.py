"""Procedurally generated sprites (no asset files needed).

Karts are tiny 3D box models rendered by a minimal software renderer into
24 directional frames (like the pre-rendered sprites of classic Mode-7 racers).
"""
import math

import pygame

from client.ui import PLAYER_COLORS
from common import config as C

from client.render import kartmodel

KART_FRAMES = 32
KART_IMG = 120         # sprite image size in pixels
KART_WORLD = 54.0      # world units covered by the sprite width
KART_GROUND_Y = 0.70   # where the ground contact point sits inside the sprite (fraction of height)


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


_FACES = [((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
          ((-1, 0, 0), [(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)]),
          ((0, 1, 0), [(-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)]),
          ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
          ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
          ((0, 0, -1), [(-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)])]


def render_model(boxes, yaw, size, world, ground_y=KART_GROUND_Y, pitch=0.42):
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    s = size / world
    cy_, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    light = (-0.35, -0.45, 0.82)
    polys = []
    ox, oy = size / 2, size * ground_y
    for (bx, by, bz, hx, hy, hz, color) in boxes:
        for n, corners in _FACES:
            nx = n[0] * cy_ - n[1] * sy_
            ny = n[0] * sy_ + n[1] * cy_
            nz = n[2]
            if nx * cp - nz * sp >= -0.01:      # facing away from the camera
                continue
            pts = []
            depth = 0.0
            for (ux, uy, uz) in corners:
                x, y, z = bx + ux * hx, by + uy * hy, bz + uz * hz
                X = x * cy_ - y * sy_
                Y = x * sy_ + y * cy_
                d = X * cp - z * sp
                up = z * cp + X * sp
                pts.append((ox + Y * s, oy - up * s))
                depth += d
            lum = 0.55 + 0.5 * max(0.0, nx * light[0] + ny * light[1] + nz * light[2])
            polys.append((depth / 4, pts, _shade(color, lum)))
    polys.sort(key=lambda p: -p[0])
    for _d, pts, col in polys:
        pygame.draw.polygon(surf, col, pts)
        pygame.draw.polygon(surf, _shade(col, 0.7), pts, 1)
    return surf


class SpriteBank:
    def __init__(self):
        self.kart_frames = {}
        self.ghost_frames = {}
        self.exhaust_anchors = None
        self._scaled = {}
        self.box_frames = [self._item_box(i * math.pi / 16) for i in range(8)]
        self.banana = self._banana()
        self.shell = self._shell((40, 200, 70))
        self.homing = self._shell((230, 50, 50), spikes=True)
        self.shield = self._shield()
        self.icons = {i: self._icon(i) for i in range(1, 7)}

    # ---------------------------------------------------------------- karts
    def _render_kart_frames(self, color_idx):
        color = PLAYER_COLORS[color_idx % len(PLAYER_COLORS)]
        mesh, exhausts = kartmodel.kart_mesh(color)
        frames, anchors = [], []
        for i in range(KART_FRAMES):
            img, pts = kartmodel.render(mesh, 2 * math.pi * i / KART_FRAMES, KART_IMG, KART_WORLD, KART_GROUND_Y,
                                        points=exhausts)
            frames.append(img)
            anchors.append(pts)
        self.kart_frames[color_idx] = frames
        self.exhaust_anchors = anchors   # identical for every colour

    def prewarm(self, color_indices):
        """Render all frames for the colours of a race up front (during the countdown)."""
        for c in color_indices:
            if c not in self.kart_frames:
                self._render_kart_frames(c)

    @staticmethod
    def frame_index(rel_angle):
        return int(round(rel_angle / (2 * math.pi) * KART_FRAMES)) % KART_FRAMES

    def kart(self, color_idx, rel_angle, ghost=False):
        if color_idx not in self.kart_frames:
            self._render_kart_frames(color_idx)
        idx = self.frame_index(rel_angle)
        if not ghost:
            return self.kart_frames[color_idx][idx]
        if color_idx not in self.ghost_frames:
            ghosts = []
            for f in self.kart_frames[color_idx]:
                g = f.copy()
                tint = pygame.Surface(g.get_size(), pygame.SRCALPHA)
                tint.fill((120, 140, 255, 0))
                g.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
                g.fill((255, 255, 255, 125), special_flags=pygame.BLEND_RGBA_MULT)
                ghosts.append(g)
            self.ghost_frames[color_idx] = ghosts
        return self.ghost_frames[color_idx][idx]

    def flame(self, size, t):
        """Animated boost flame (two-layer teardrop), cached per size bucket and animation phase."""
        phase = int(t * 20) % 4
        size = max(4, int(size) // 2 * 2)
        key = ("flame", size, phase)
        f = self._scaled.get(key)
        if f is None:
            h = int(size * (1.0 + 0.18 * phase))
            f = pygame.Surface((size, h + size // 2), pygame.SRCALPHA)
            for scale, col in ((1.0, (255, 90, 20, 200)), (0.68, (255, 190, 40, 230)), (0.36, (255, 255, 210, 255))):
                w = max(2, int(size * scale))
                hh = max(2, int(h * scale))
                r = pygame.Rect(0, 0, w, hh)
                r.midtop = (size // 2, (h + size // 2 - hh) // 2)
                pygame.draw.ellipse(f, col, r)
            self._scaled[key] = f
        return f

    def scaled(self, img, w, key):
        """Scaled copy of a sprite, cached by (key, width bucket)."""
        w = max(2, int(w))
        if w > 600:
            w = 600
        # bucket the width (finer when small) so the cache is reused across frames
        if w > 160:
            w = w // 8 * 8
        elif w > 60:
            w = w // 4 * 4
        elif w > 24:
            w = w // 2 * 2
        ck = (key, w)
        out = self._scaled.get(ck)
        if out is None:
            h = max(1, int(img.get_height() * w / img.get_width()))
            # enlarging -> smooth (no blocky pixels close to the camera); shrinking -> fast
            if w > img.get_width() * 0.9:
                out = pygame.transform.smoothscale(img, (w, h))
            else:
                out = pygame.transform.scale(img, (w, h))
            if len(self._scaled) > 4000:
                self._scaled.clear()
            self._scaled[ck] = out
        return out

    # ---------------------------------------------------------------- items
    def _item_box(self, yaw):
        boxes = [(0, 0, 9, 9, 9, 9, (90, 170, 255))]
        surf = render_model(boxes, yaw, 64, 40, ground_y=0.86, pitch=0.35)
        f = pygame.font.SysFont("arialblack,arial", 30, bold=True)
        q = f.render("?", True, (255, 255, 255))
        surf.blit(q, q.get_rect(center=(32, 30)))
        glow = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        glow.fill((255, 255, 255, 0))
        return surf

    def _banana(self):
        s = pygame.Surface((40, 40), pygame.SRCALPHA)
        pygame.draw.arc(s, (250, 220, 40), pygame.Rect(4, -10, 32, 40), math.pi * 1.05, math.pi * 1.95, 9)
        pygame.draw.arc(s, (200, 160, 20), pygame.Rect(4, -10, 32, 40), math.pi * 1.05, math.pi * 1.95, 2)
        pygame.draw.circle(s, (90, 60, 20), (7, 17), 3)
        pygame.draw.circle(s, (90, 60, 20), (33, 17), 3)
        return s

    def _shell(self, color, spikes=False):
        s = pygame.Surface((40, 40), pygame.SRCALPHA)
        pygame.draw.ellipse(s, (0, 0, 0, 90), (4, 30, 32, 9))
        pygame.draw.circle(s, _shade(color, 0.7), (20, 21), 15)
        pygame.draw.circle(s, color, (20, 19), 14)
        pygame.draw.circle(s, _shade(color, 1.4), (15, 14), 5)
        pygame.draw.rect(s, (250, 250, 240), (5, 24, 30, 6), border_radius=3)
        if spikes:
            for a in range(0, 360, 60):
                x = 20 + math.cos(math.radians(a)) * 12
                y = 19 + math.sin(math.radians(a)) * 12
                pygame.draw.circle(s, (255, 255, 255), (int(x), int(y)), 3)
        return s

    def _shield(self):
        s = pygame.Surface((96, 96), pygame.SRCALPHA)
        for r, a in ((46, 40), (42, 50), (36, 30)):
            pygame.draw.circle(s, (100, 220, 255, a), (48, 48), r)
        pygame.draw.circle(s, (180, 250, 255, 160), (48, 48), 46, 3)
        return s

    # ---------------------------------------------------------------- HUD icons
    def _icon(self, item):
        s = pygame.Surface((64, 64), pygame.SRCALPHA)
        if item in (C.ITEM_BOOST, C.ITEM_TRIPLE):
            n = 3 if item == C.ITEM_TRIPLE else 1
            for i in range(n):
                dx = (i - (n - 1) / 2) * 16
                pts = [(32 + dx, 6), (44 + dx, 34), (36 + dx, 34), (40 + dx, 58), (22 + dx, 26), (30 + dx, 26)]
                pygame.draw.polygon(s, (255, 140, 20), pts)
                pygame.draw.polygon(s, (255, 230, 80), pts, 3)
        elif item == C.ITEM_BANANA:
            s.blit(pygame.transform.smoothscale(self.banana, (60, 60)), (2, 6))
        elif item == C.ITEM_SHELL:
            s.blit(pygame.transform.smoothscale(self.shell, (56, 56)), (4, 4))
        elif item == C.ITEM_HOMING:
            s.blit(pygame.transform.smoothscale(self.homing, (56, 56)), (4, 4))
        elif item == C.ITEM_SHIELD:
            s.blit(pygame.transform.smoothscale(self.shield, (60, 60)), (2, 2))
        return s


# ---------------------------------------------------------------- scenery / props / effects
# Everything is drawn at 2x and smooth-scaled down, so edges are anti-aliased.

def _aa(surf):
    return pygame.transform.smoothscale(surf, (surf.get_width() // 2, surf.get_height() // 2))


def _pine(shade):
    s = pygame.Surface((140, 240), pygame.SRCALPHA)
    pygame.draw.rect(s, (92, 60, 34), (62, 170, 16, 66))
    pygame.draw.rect(s, (70, 44, 24), (70, 170, 8, 66))
    layers = [(132, 196, 60), (110, 150, 40), (84, 104, 22)]
    for i, (w, base, top) in enumerate(layers):
        light = _shade((46, 132, 62), shade + i * 0.07)
        dark = _shade(light, 0.68)
        pygame.draw.polygon(s, light, [(70, top), (70 - w // 2, base), (70, base - 6)])
        pygame.draw.polygon(s, dark, [(70, top), (70 + w // 2, base), (70, base - 6)])
    return _aa(s)


def _round_tree(shade):
    s = pygame.Surface((180, 220), pygame.SRCALPHA)
    pygame.draw.rect(s, (96, 64, 38), (80, 130, 20, 88))
    pygame.draw.rect(s, (72, 46, 26), (90, 130, 10, 88))
    base = _shade((58, 140, 58), shade)
    for cx, cy, r in ((90, 92, 62), (52, 112, 42), (128, 112, 42), (90, 58, 44)):
        pygame.draw.circle(s, _shade(base, 0.72), (cx + 6, cy + 6), r)
        pygame.draw.circle(s, base, (cx, cy), r)
        pygame.draw.circle(s, _shade(base, 1.22), (cx - r // 3, cy - r // 3), r // 2)
    return _aa(s)


def _bush():
    s = pygame.Surface((160, 90), pygame.SRCALPHA)
    for cx, cy, r in ((44, 58, 32), (80, 44, 40), (118, 58, 32)):
        pygame.draw.circle(s, (44, 118, 50), (cx + 4, cy + 4), r)
        pygame.draw.circle(s, (66, 152, 66), (cx, cy), r)
        pygame.draw.circle(s, (98, 186, 92), (cx - r // 3, cy - r // 3), r // 2)
    return _aa(s)


def _cactus(h):
    s = pygame.Surface((120, 240), pygame.SRCALPHA)
    g, d, lt = (64, 146, 74), (44, 108, 54), (104, 186, 110)
    top = 240 - h * 2

    def limb(x, y, w, hh):
        pygame.draw.rect(s, g, (x, y, w, hh), border_radius=w // 2)
        pygame.draw.rect(s, d, (x + int(w * 0.6), y + 6, int(w * 0.4), hh - 12), border_radius=6)
        pygame.draw.rect(s, lt, (x + int(w * 0.18), y + 8, max(2, int(w * 0.16)), hh - 16), border_radius=4)
    limb(44, top, 32, 240 - top)
    limb(10, top + 60, 22, 70)
    pygame.draw.rect(s, g, (14, top + 118, 40, 16))
    limb(88, top + 40, 22, 64)
    pygame.draw.rect(s, g, (66, top + 92, 34, 16))
    return _aa(s)


def _rock():
    s = pygame.Surface((160, 100), pygame.SRCALPHA)
    pts = [(10, 96), (24, 50), (60, 18), (104, 22), (140, 52), (152, 96)]
    pygame.draw.polygon(s, (138, 92, 64), pts)
    pygame.draw.polygon(s, (176, 128, 92), [(24, 50), (60, 18), (104, 22), (80, 60), (40, 70)])
    pygame.draw.polygon(s, (110, 72, 50), [(104, 22), (140, 52), (152, 96), (96, 96), (80, 60)])
    return _aa(s)


def _neon_pole(c):
    s = pygame.Surface((80, 300), pygame.SRCALPHA)
    for r, a in ((36, 30), (26, 50), (16, 90)):
        pygame.draw.circle(s, (*c, a), (40, 24), r)
    pygame.draw.rect(s, (36, 34, 58), (32, 24, 16, 276))
    pygame.draw.rect(s, c, (36, 30, 8, 264))
    pygame.draw.rect(s, (255, 255, 255), (38, 30, 3, 264))
    pygame.draw.circle(s, (255, 255, 255), (40, 24), 10)
    return _aa(s)


def _tower():
    s = pygame.Surface((180, 320), pygame.SRCALPHA)
    pygame.draw.rect(s, (28, 26, 50), (10, 0, 160, 320))
    pygame.draw.rect(s, (20, 18, 38), (110, 0, 60, 320))
    for yy in range(16, 304, 22):
        for xx in range(24, 156, 26):
            on = (xx * 7 + yy * 13) % 5 != 0
            col = (255, 214, 120) if on else (44, 44, 70)
            if on and xx > 110:
                col = (90, 210, 255)
            pygame.draw.rect(s, col, (xx, yy, 12, 12))
    return _aa(s)


def scenery_sprites(theme):
    """Returns list of (surface, world_height) variants for the theme's off-track decoration."""
    if theme == "grass":
        return [(_pine(0.9), 105.0), (_pine(1.05), 120.0), (_round_tree(1.0), 96.0),
                (_round_tree(0.85), 84.0), (_bush(), 30.0)]
    if theme == "desert":
        return [(_cactus(100), 92.0), (_cactus(80), 74.0), (_rock(), 42.0), (_rock(), 30.0)]
    return [(_neon_pole((255, 60, 220)), 130.0), (_neon_pole((60, 230, 255)), 130.0),
            (_neon_pole((255, 220, 60)), 120.0), (_tower(), 190.0)]


def _tyre_stack():
    s = pygame.Surface((90, 100), pygame.SRCALPHA)
    for i, band in enumerate(((230, 40, 40), (245, 245, 245), (230, 40, 40))):
        y = 70 - i * 26
        pygame.draw.ellipse(s, (22, 22, 26), (6, y, 78, 30))
        pygame.draw.rect(s, (30, 30, 34), (6, y - 8, 78, 22))
        pygame.draw.rect(s, band, (6, y - 2, 78, 8))
        pygame.draw.ellipse(s, (46, 46, 52), (6, y - 20, 78, 26))
    pygame.draw.ellipse(s, (12, 12, 14), (28, 4, 34, 12))
    return _aa(s)


def _cone():
    s = pygame.Surface((60, 80), pygame.SRCALPHA)
    pygame.draw.rect(s, (230, 90, 20), (4, 70, 52, 8), border_radius=3)
    pygame.draw.polygon(s, (255, 120, 30), [(30, 4), (10, 72), (50, 72)])
    pygame.draw.polygon(s, (205, 80, 20), [(30, 4), (30, 72), (50, 72)])
    pygame.draw.polygon(s, (250, 250, 250), [(22, 32), (38, 32), (42, 46), (18, 46)])
    return _aa(s)


def _bale():
    s = pygame.Surface((100, 80), pygame.SRCALPHA)
    pygame.draw.rect(s, (214, 178, 84), (4, 10, 92, 66), border_radius=10)
    pygame.draw.rect(s, (180, 146, 60), (60, 10, 36, 66), border_radius=10)
    for x in range(12, 92, 10):
        pygame.draw.line(s, (236, 206, 120), (x, 14), (x - 4, 72), 2)
    pygame.draw.line(s, (120, 90, 40), (30, 10), (30, 76), 3)
    pygame.draw.line(s, (120, 90, 40), (70, 10), (70, 76), 3)
    return _aa(s)


def _barrel():
    s = pygame.Surface((60, 90), pygame.SRCALPHA)
    pygame.draw.rect(s, (190, 40, 30), (6, 8, 48, 78), border_radius=8)
    pygame.draw.rect(s, (140, 26, 20), (36, 8, 18, 78), border_radius=8)
    for y in (24, 60):
        pygame.draw.rect(s, (110, 20, 16), (6, y, 48, 5))
    pygame.draw.ellipse(s, (120, 30, 24), (6, 2, 48, 14))
    return _aa(s)


def _bollard(c):
    s = pygame.Surface((50, 110), pygame.SRCALPHA)
    for r, a in ((24, 40), (16, 70)):
        pygame.draw.circle(s, (*c, a), (25, 30), r)
    pygame.draw.rect(s, (40, 38, 62), (15, 30, 20, 76), border_radius=6)
    pygame.draw.rect(s, c, (15, 34, 20, 12), border_radius=4)
    pygame.draw.rect(s, c, (15, 62, 20, 8), border_radius=3)
    return _aa(s)


def prop_sprites(theme):
    """Trackside objects lining both edges: list of 2 (surface, world_height) variants."""
    if theme == "grass":
        return [(_tyre_stack(), 30.0), (_cone(), 18.0)]
    if theme == "desert":
        return [(_bale(), 24.0), (_barrel(), 26.0)]
    return [(_bollard((255, 50, 210)), 30.0), (_bollard((50, 230, 255)), 30.0)]


def gantry_sprite(world_width, theme):
    """Start/finish gantry spanning the road (billboard). Returns (surface, world_height)."""
    W = 900
    px = W / world_width
    H = int(110 * px)
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    pillar = (60, 62, 72) if theme != "neon" else (40, 36, 70)
    pw = int(10 * px)
    for x in (0, W - pw):
        pygame.draw.rect(s, pillar, (x, 0, pw, H))
        pygame.draw.rect(s, _shade(pillar, 1.4), (x, 0, max(1, pw // 3), H))
    top = int(4 * px)
    bh = int(30 * px)
    pygame.draw.rect(s, (20, 22, 30), (0, top, W, bh))
    sq = max(2, bh // 4)
    for i in range(0, W // sq + 1):
        for j in range(2):
            if (i + j) % 2 == 0:
                pygame.draw.rect(s, (245, 245, 245), (i * sq, top + j * sq, sq, sq))
                pygame.draw.rect(s, (245, 245, 245), (i * sq, top + bh - (j + 1) * sq, sq, sq))
    accent = {"grass": (220, 40, 40), "desert": (240, 130, 30), "neon": (255, 50, 210)}.get(theme, (220, 40, 40))
    band = pygame.Rect(int(W * 0.18), top + sq * 2 - 2, int(W * 0.64), max(8, bh - sq * 4 + 4))
    pygame.draw.rect(s, accent, band)
    f = pygame.font.SysFont("arialblack,impact,arial", max(10, band.h - 4), bold=True)
    txt = f.render("TURBO KART ARENA", True, (255, 255, 255))
    if txt.get_width() > band.w - 8:
        txt = pygame.transform.smoothscale(txt, (band.w - 8, max(1, int(txt.get_height() * (band.w - 8) / txt.get_width()))))
    s.blit(txt, txt.get_rect(center=band.center))
    return s, H / px


def shadow_sprite():
    s = pygame.Surface((128, 40), pygame.SRCALPHA)
    for i in range(20, 0, -1):
        a = int(110 * (1 - i / 20) ** 0.7)
        pygame.draw.ellipse(s, (0, 0, 0, a), pygame.Rect(64 - i * 3.2, 20 - i, i * 6.4, i * 2))
    return s


def soft_dot(color, size=32):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    r = size // 2
    for i in range(r, 0, -1):
        a = int(255 * (1 - i / r) ** 1.6)
        pygame.draw.circle(s, (*color, a), (r, r), i)
    return s
