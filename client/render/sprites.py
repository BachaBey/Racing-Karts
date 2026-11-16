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
        ck = (key, w)
        out = self._scaled.get(ck)
        if out is None:
            h = max(1, int(img.get_height() * w / img.get_width()))
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


# ---------------------------------------------------------------- scenery
def scenery_sprites(theme):
    """Returns list of (surface, world_height) variants for the theme."""
    out = []
    if theme == "grass":
        for shade in (0.85, 1.0, 1.15):
            s = pygame.Surface((64, 110), pygame.SRCALPHA)
            pygame.draw.rect(s, (110, 70, 35), (27, 70, 10, 38))
            for i, (w, y) in enumerate(((60, 52), (50, 32), (36, 12))):
                c = _shade((40, 150, 60), shade + i * 0.08)
                pygame.draw.polygon(s, c, [(32, y - 14), (32 - w // 2, y + 26), (32 + w // 2, y + 26)])
            out.append((s, 95.0))
        b = pygame.Surface((80, 50), pygame.SRCALPHA)
        for cx, cy, r in ((22, 30, 18), (40, 22, 22), (58, 30, 18)):
            pygame.draw.circle(b, (60, 170, 70), (cx, cy), r)
            pygame.draw.circle(b, (90, 200, 90), (cx - 4, cy - 5), r // 2)
        out.append((b, 34.0))
    elif theme == "desert":
        for h in (90, 70):
            s = pygame.Surface((60, 110), pygame.SRCALPHA)
            g = (60, 150, 70)
            pygame.draw.rect(s, g, (22, 110 - h, 16, h), border_radius=8)
            pygame.draw.rect(s, g, (6, 50, 10, 30), border_radius=5)
            pygame.draw.rect(s, g, (6, 72, 22, 8))
            pygame.draw.rect(s, g, (44, 38, 10, 30), border_radius=5)
            pygame.draw.rect(s, g, (34, 60, 20, 8))
            pygame.draw.line(s, (90, 190, 100), (27, 112 - h), (27, 106), 2)
            out.append((s, h * 1.0))
        r = pygame.Surface((80, 50), pygame.SRCALPHA)
        pygame.draw.ellipse(r, (150, 100, 70), (4, 10, 72, 40))
        pygame.draw.ellipse(r, (180, 130, 90), (14, 12, 40, 20))
        out.append((r, 40.0))
    else:
        for c in ((255, 60, 220), (60, 230, 255), (255, 230, 60)):
            s = pygame.Surface((40, 140), pygame.SRCALPHA)
            pygame.draw.rect(s, (30, 30, 50), (12, 10, 16, 130))
            pygame.draw.rect(s, c, (16, 14, 8, 122))
            pygame.draw.rect(s, (255, 255, 255), (18, 14, 4, 122))
            pygame.draw.circle(s, c, (20, 10), 10)
            out.append((s, 120.0))
        b = pygame.Surface((90, 120), pygame.SRCALPHA)
        pygame.draw.rect(b, (25, 25, 45), (5, 0, 80, 120))
        for yy in range(8, 112, 16):
            for xx in range(12, 80, 18):
                col = (255, 220, 120) if (xx * yy) % 7 else (80, 200, 255)
                pygame.draw.rect(b, col, (xx, yy, 8, 8))
        out.append((b, 160.0))
    return out
