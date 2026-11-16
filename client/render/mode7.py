"""Pseudo-3D "Mode-7" renderer.

The track is painted once into a 2048x2048 texture.  Every frame, for each
screen pixel below the horizon we compute which world point it sees
(perspective projection of a flat floor) and fetch that texel.  The whole
per-pixel computation is vectorised with numpy, so it is a handful of array
operations per frame.  Sprites (karts, items, scenery) are billboards projected
with the same camera.
"""
import math
import random

import numpy as np
import pygame

from common.track import MAP_SIZE

THEMES = {
    "grass": {"g1": (78, 168, 64), "g2": (68, 154, 56), "road": (112, 112, 122), "road2": (102, 102, 112),
              "k1": (225, 45, 45), "k2": (245, 245, 245), "out": (40, 110, 190), "line": (240, 240, 240),
              "sky1": (90, 160, 255), "sky2": (190, 225, 255), "hill": (60, 130, 90), "hill2": (90, 160, 110),
              "fog": (190, 225, 255)},
    "desert": {"g1": (230, 196, 128), "g2": (218, 182, 114), "road": (126, 108, 98), "road2": (116, 98, 90),
               "k1": (240, 140, 40), "k2": (250, 240, 220), "out": (196, 150, 92), "line": (255, 230, 150),
               "sky1": (250, 150, 80), "sky2": (255, 220, 160), "hill": (190, 110, 70), "hill2": (220, 150, 90),
               "fog": (255, 214, 160)},
    "neon": {"g1": (20, 16, 44), "g2": (26, 20, 56), "road": (44, 44, 66), "road2": (38, 38, 58),
             "k1": (255, 40, 200), "k2": (40, 220, 255), "out": (6, 4, 18), "line": (255, 255, 120),
             "sky1": (10, 6, 30), "sky2": (70, 20, 90), "hill": (30, 20, 60), "hill2": (50, 30, 90),
             "fog": (60, 20, 80)},
}


class TrackArt:
    """Pre-rendered texture + minimap + sky for one track."""

    def __init__(self, track):
        self.track = track
        th = self.theme = THEMES.get(track.theme, THEMES["grass"])
        S = MAP_SIZE
        surf = pygame.Surface((S, S)).convert() if pygame.display.get_surface() else pygame.Surface((S, S), 0, 32)
        surf.fill(th["g1"])
        for gx in range(0, S, 64):
            for gy in range(0, S, 64):
                if (gx // 64 + gy // 64) % 2:
                    surf.fill(th["g2"], (gx, gy, 64, 64))
        if track.theme == "neon":
            for g in range(0, S, 64):
                pygame.draw.line(surf, (60, 30, 110), (g, 0), (g, S), 2)
                pygame.draw.line(surf, (60, 30, 110), (0, g), (S, g), 2)
        rng = random.Random(track.key + "tex")
        # dense centerline samples
        dense = []
        n = track.n
        for i in range(n):
            ax, ay = track.pts[i]
            bx, by = track.pts[(i + 1) % n]
            for k in range(4):
                t = k / 4
                dense.append((ax + (bx - ax) * t, ay + (by - ay) * t, track.cum[i] + track.seg_len[i] * t))
        half = track.half
        for x, y, s in dense:
            c = th["k1"] if int(s / 36) % 2 else th["k2"]
            pygame.draw.circle(surf, c, (int(x), int(y)), int(half + 14))
        for x, y, s in dense:
            pygame.draw.circle(surf, th["road"], (int(x), int(y)), int(half))
        for _ in range(900):   # asphalt texture speckles
            x, y, _s = dense[rng.randrange(len(dense))]
            ox, oy = rng.uniform(-half * 0.8, half * 0.8), rng.uniform(-half * 0.8, half * 0.8)
            pygame.draw.circle(surf, th["road2"], (int(x + ox), int(y + oy)), rng.randint(3, 9))
        for x, y, s in dense:
            if int(s / 50) % 2 == 0:
                pygame.draw.circle(surf, th["line"], (int(x), int(y)), 3)
        # finish line (checkered band across the road at progress 0)
        fx, fy, h = track.point_at(0.0)
        fwd = (math.cos(h), math.sin(h))
        right = (-math.sin(h), math.cos(h))
        sq = 14
        cols = int(track.width // sq) + 1
        for row in range(3):
            for col in range(cols):
                lat = -track.width / 2 + col * sq
                lon = (row - 1.5) * sq
                pts = []
                for (a, b) in ((lat, lon), (lat + sq, lon), (lat + sq, lon + sq), (lat, lon + sq)):
                    pts.append((fx + right[0] * a + fwd[0] * b, fy + right[1] * a + fwd[1] * b))
                pygame.draw.polygon(surf, (250, 250, 250) if (row + col) % 2 else (20, 20, 20), pts)
        # grid marks
        for slot in range(6):
            gx, gy, gh = track.grid_slot(slot)
            r = (-math.sin(gh), math.cos(gh))
            f = (math.cos(gh), math.sin(gh))
            p1 = (gx + r[0] * 18 + f[0] * 22, gy + r[1] * 18 + f[1] * 22)
            p2 = (gx - r[0] * 18 + f[0] * 22, gy - r[1] * 18 + f[1] * 22)
            pygame.draw.line(surf, (240, 240, 240), p1, p2, 4)
        # boost pads (chevrons)
        for (px, py, ph) in track.pads:
            f = (math.cos(ph), math.sin(ph))
            r = (-math.sin(ph), math.cos(ph))
            base = [(-34, -26), (34, -26), (34, 26), (-34, 26)]
            pts = [(px + f[0] * a + r[0] * b, py + f[1] * a + r[1] * b) for a, b in base]
            pygame.draw.polygon(surf, (255, 150, 20), pts)
            for k in (-16, 4, 24):
                chev = [(k - 12, -18), (k, 0), (k - 12, 18)]
                cp = [(px + f[0] * a + r[0] * b, py + f[1] * a + r[1] * b) for a, b in chev]
                pygame.draw.lines(surf, (255, 240, 90), False, cp, 6)
        self.surface = surf
        self.flat = pygame.surfarray.array2d(surf).ravel().copy()   # mapped pixel ints, index = x*S + y
        self.out_color = surf.map_rgb(th["out"])
        self._make_minimap()

    def _make_minimap(self, size=170):
        t = self.track
        mm = pygame.Surface((size, size), pygame.SRCALPHA)
        sc = (size - 16) / MAP_SIZE
        pts = [(8 + x * sc, 8 + y * sc) for x, y in t.pts]
        pygame.draw.polygon(mm, (0, 0, 0, 120), [(0, 0), (size, 0), (size, size), (0, size)])
        pygame.draw.lines(mm, (30, 30, 40), True, pts, max(3, int(t.width * sc) + 2))
        pygame.draw.lines(mm, (210, 210, 220), True, pts, max(2, int(t.width * sc) - 1))
        fx, fy, _ = t.point_at(0)
        pygame.draw.circle(mm, (255, 255, 255), (int(8 + fx * sc), int(8 + fy * sc)), 4)
        self.minimap = mm
        self.mm_scale = sc


class Mode7View:
    def __init__(self, w, h, art, fmt_surface):
        self.w, self.h = w, h
        self.art = art
        wide = w / h > 2.5            # split-screen viewport
        self.horizon = int(h * (0.30 if wide else 0.36))
        self.focal = (w / 2) / math.tan(math.radians(38 if wide else 36))
        self.cam_back = 160.0
        # choose the camera height so the player's kart sits at ~80% of the viewport height
        self.cam_h = (h * 0.80 - self.horizon) * self.cam_back / self.focal
        rows = np.arange(self.horizon + 1, h, dtype=np.float32)
        self.R = len(rows)
        self.rowz = (self.cam_h * self.focal / (rows - self.horizon)).astype(np.float32)
        self.colk = ((np.arange(w, dtype=np.float32) - w / 2) / self.focal).astype(np.float32)
        self.floor = pygame.Surface((w, self.R), 0, fmt_surface)
        th = art.theme
        # fog: alpha gradient strongest at the horizon
        self.fog = pygame.Surface((w, self.R), pygame.SRCALPHA)
        for i in range(self.R):
            z = self.rowz[i]
            a = int(max(0.0, min(1.0, (z - 500) / 1400.0)) * 235)
            pygame.draw.line(self.fog, (*th["fog"], a), (0, i), (w, i))
        self._make_sky(th)

    def _make_sky(self, th):
        fov = 2 * math.atan((self.w / 2) / self.focal)
        self.pano_w = int(self.w * 2 * math.pi / fov)
        hz = self.horizon + 1
        sky = pygame.Surface((self.w, hz))
        for y in range(hz):
            f = y / max(1, hz - 1)
            c = [int(th["sky1"][i] * (1 - f) + th["sky2"][i] * f) for i in range(3)]
            pygame.draw.line(sky, c, (0, y), (self.w, y))
        self.sky = sky
        pano = pygame.Surface((self.pano_w, hz), pygame.SRCALPHA)
        rng = random.Random(self.art.track.key + "sky")
        if self.art.track.theme == "neon":
            x = 0
            while x < self.pano_w:
                bw = rng.randint(14, 40)
                bh = rng.randint(int(hz * 0.15), int(hz * 0.6))
                pygame.draw.rect(pano, th["hill2"], (x, hz - bh, bw, bh))
                for wy in range(hz - bh + 4, hz - 4, 7):
                    for wx in range(x + 3, x + bw - 3, 6):
                        if rng.random() < 0.35:
                            pano.set_at((wx, wy), (255, 220, 120))
                            pano.set_at((wx + 1, wy), (255, 220, 120))
                x += bw + rng.randint(0, 6)
        else:
            for layer, col, amp in ((0, th["hill"], 0.55), (1, th["hill2"], 0.3)):
                pts = [(0, hz)]
                y = hz * (1 - amp * 0.5)
                for x in range(0, self.pano_w + 8, 8):
                    y += rng.uniform(-6, 6)
                    y = max(hz * (1 - amp), min(hz - 4, y))
                    pts.append((x, y))
                pts.append((self.pano_w, hz))
                # make it wrap seamlessly
                pts[-2] = (pts[-2][0], pts[1][1])
                pygame.draw.polygon(pano, col, pts)
        self.pano = pano

    def render(self, surf, cx, cy, ca):
        fx, fy = math.cos(ca), math.sin(ca)
        rx, ry = -fy, fx
        Z = self.rowz[None, :]
        L = self.colk[:, None] * Z
        wx = (cx + fx * Z + rx * L).astype(np.int32)
        wy = (cy + fy * Z + ry * L).astype(np.int32)
        S = MAP_SIZE
        oob = (wx < 0) | (wx >= S) | (wy < 0) | (wy >= S)
        np.clip(wx, 0, S - 1, out=wx)
        np.clip(wy, 0, S - 1, out=wy)
        pix = self.art.flat[wx * S + wy]
        pix[oob] = self.art.out_color
        pygame.surfarray.blit_array(self.floor, pix)
        surf.blit(self.sky, (0, 0))
        off = int((ca / (2 * math.pi)) * self.pano_w) % self.pano_w
        surf.blit(self.pano, (-off, 0))
        surf.blit(self.pano, (-off + self.pano_w, 0))
        surf.blit(self.floor, (0, self.horizon + 1))
        surf.blit(self.fog, (0, self.horizon + 1))

    def project(self, cx, cy, ca, x, y):
        """World point -> (screen_x, screen_y_of_ground, pixels_per_world_unit, depth) or None."""
        dx, dy = x - cx, y - cy
        fx, fy = math.cos(ca), math.sin(ca)
        z = dx * fx + dy * fy
        if z < 20:
            return None
        lat = dx * -fy + dy * fx
        k = self.focal / z
        sx = self.w / 2 + lat * k
        if sx < -200 or sx > self.w + 200:
            return None
        return sx, self.horizon + self.cam_h * k, k, z
