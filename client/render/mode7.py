"""Pseudo-3D "Mode-7" renderer.

The track is painted once into a 2048x2048 texture (zones drawn with pygame, then
coloured with numpy noise for a realistic look: mowed grass, asphalt grain, gravel
run-off, shaded kerbs).  Every frame, for each screen pixel below the horizon we
compute which world point it sees and fetch that texel - fully vectorised with numpy.

Mip-maps: far rows sample pre-blurred half / quarter resolution textures, which
removes the shimmering of distant texture detail when driving fast.
"""
import math
import random

import numpy as np
import pygame

from common.track import MAP_SIZE

THEMES = {
    "grass": {"ground": (86, 150, 62), "shoulder": (184, 168, 136), "road": (88, 90, 96),
              "k1": (206, 38, 38), "k2": (238, 238, 238), "line": (236, 236, 236), "out": (34, 92, 150),
              "sky1": (58, 118, 214), "sky2": (178, 214, 245), "far": (122, 152, 186), "near": (64, 112, 82),
              "fog": (186, 214, 236), "sun": (255, 246, 214), "cloud": (255, 255, 255)},
    "desert": {"ground": (220, 184, 128), "shoulder": (186, 146, 98), "road": (112, 102, 96),
               "k1": (232, 120, 30), "k2": (246, 238, 222), "line": (244, 226, 160), "out": (190, 148, 92),
               "sky1": (232, 128, 72), "sky2": (255, 214, 158), "far": (204, 140, 112), "near": (168, 98, 66),
               "fog": (250, 206, 160), "sun": (255, 236, 180), "cloud": (255, 226, 200)},
    "neon": {"ground": (20, 15, 42), "shoulder": (32, 22, 62), "road": (36, 36, 54),
             "k1": (255, 40, 200), "k2": (40, 220, 255), "line": (60, 240, 255), "out": (6, 4, 18),
             "sky1": (8, 5, 26), "sky2": (64, 18, 86), "far": (38, 22, 70), "near": (24, 16, 48),
             "fog": (52, 18, 74), "sun": (255, 80, 200), "cloud": (120, 60, 160)},
}

# zone codes painted into the red channel of the zone map
Z_GROUND, Z_SHOULDER, Z_KERB1, Z_KERB2, Z_LINE, Z_ROAD, Z_RACELINE = 0, 30, 60, 90, 120, 150, 180


def _smooth_noise(rng, cells, size):
    """Smooth value noise in [0,1]: random grid upscaled with bilinear smoothscale."""
    small = (rng.random((cells, cells)) * 255).astype(np.uint8)
    surf = pygame.surfarray.make_surface(np.dstack([small] * 3))
    big = pygame.transform.smoothscale(surf, (size, size))
    return pygame.surfarray.pixels_red(big).astype(np.float32) / 255.0


class TrackArt:
    """Pre-rendered texture (+ mip-maps) + minimap + trackside props for one track."""

    def __init__(self, track):
        self.track = track
        th = self.theme = THEMES.get(track.theme, THEMES["grass"])
        S = MAP_SIZE
        rng = np.random.default_rng(sum(ord(c) * 131 ** i for i, c in enumerate(track.key)) % (2 ** 32))
        prng = random.Random(track.key + "tex")

        # ---- 1. zone map (pygame drawing) -----------------------------------------
        zones = pygame.Surface((S, S))
        zones.fill((Z_GROUND, 0, 0))
        dense = []
        for i in range(track.n):
            ax, ay = track.pts[i]
            bx, by = track.pts[(i + 1) % track.n]
            for k in range(4):
                t = k / 4
                dense.append((ax + (bx - ax) * t, ay + (by - ay) * t, track.cum[i] + track.seg_len[i] * t))
        half = track.half
        for x, y, s in dense:
            pygame.draw.circle(zones, (Z_SHOULDER, 0, 0), (int(x), int(y)), int(half + 46))
        for x, y, s in dense:
            pygame.draw.circle(zones, (Z_KERB1 if int(s / 34) % 2 else Z_KERB2, 0, 0), (int(x), int(y)), int(half + 13))
        for x, y, s in dense:
            pygame.draw.circle(zones, (Z_LINE, 0, 0), (int(x), int(y)), int(half - 1))
        for x, y, s in dense:
            pygame.draw.circle(zones, (Z_ROAD, 0, 0), (int(x), int(y)), int(half - 7))
        for x, y, s in dense:   # darker rubbered racing line
            pygame.draw.circle(zones, (Z_RACELINE, 0, 0), (int(x), int(y)), int(half * 0.32))
        zone = pygame.surfarray.pixels_red(zones).copy()
        del zones

        # ---- 2. colour with noise (numpy) -------------------------------------------
        n_large = _smooth_noise(rng, 20, S)
        n_mid = _smooth_noise(rng, 140, S)
        fine = rng.random((S, S), dtype=np.float32)
        img = np.empty((S, S, 3), np.float32)

        def paint(mask, color, k):
            for c in range(3):
                img[..., c][mask] = color[c] * k[mask] if isinstance(k, np.ndarray) else color[c] * k

        X = np.arange(S, dtype=np.int32)[:, None]
        Y = np.arange(S, dtype=np.int32)[None, :]
        if track.theme == "grass":
            stripes = np.where(((X + Y) // 72) % 2 == 0, 1.06, 0.95).astype(np.float32)   # mowing pattern
            gk = stripes * (1 + 0.20 * (n_large - 0.5) + 0.10 * (n_mid - 0.5) + 0.10 * (fine - 0.5))
        elif track.theme == "desert":
            ripples = np.sin((X * 0.55 + Y) / 7.0 + n_mid * 9.0).astype(np.float32)
            gk = 1 + 0.05 * ripples + 0.16 * (n_large - 0.5) + 0.06 * (fine - 0.5)
        else:
            grid = (((X % 64) < 2) | ((Y % 64) < 2)).astype(np.float32)
            gk = 1 + 0.25 * (n_large - 0.5) + 0.15 * (fine - 0.5) + grid * 2.2
        paint(zone == Z_GROUND, th["ground"], gk)
        paint(zone == Z_SHOULDER, th["shoulder"], 1 + 0.35 * (fine - 0.5) + 0.12 * (n_mid - 0.5))
        kerb_k = 1 + 0.08 * (fine - 0.5)
        paint(zone == Z_KERB1, th["k1"], kerb_k)
        paint(zone == Z_KERB2, th["k2"], kerb_k)
        paint(zone == Z_LINE, th["line"], 1 + 0.06 * (fine - 0.5))
        road_k = 1 + 0.16 * (fine - 0.5) + 0.10 * (n_mid - 0.5) + 0.08 * (n_large - 0.5)
        paint(zone == Z_ROAD, th["road"], road_k)
        paint(zone == Z_RACELINE, th["road"], road_k * 0.84)
        np.clip(img, 0, 255, out=img)
        surf = pygame.surfarray.make_surface(img.astype(np.uint8))
        del img, fine, n_large, n_mid, zone
        if pygame.display.get_surface():
            surf = surf.convert()

        # ---- 3. markings drawn on top -----------------------------------------------
        if track.theme == "desert":
            for x, y, s in dense:
                if int(s / 46) % 2 == 0:
                    pygame.draw.circle(surf, (236, 206, 90), (int(x), int(y)), 3)
        fx, fy, h = track.point_at(0.0)
        fwd = (math.cos(h), math.sin(h))
        right = (-math.sin(h), math.cos(h))
        sq = 12
        cols = int(track.width // sq) + 1
        for row in range(3):
            for col in range(cols):
                lat = -track.width / 2 + col * sq
                lon = (row - 1.5) * sq
                pts = [(fx + right[0] * a + fwd[0] * b, fy + right[1] * a + fwd[1] * b)
                       for (a, b) in ((lat, lon), (lat + sq, lon), (lat + sq, lon + sq), (lat, lon + sq))]
                pygame.draw.polygon(surf, (245, 245, 245) if (row + col) % 2 else (24, 24, 26), pts)
        for slot in range(6):    # starting grid boxes
            gx, gy, gh = track.grid_slot(slot)
            r = (-math.sin(gh), math.cos(gh))
            f = (math.cos(gh), math.sin(gh))
            box = [(-4, -20), (24, -20), (24, 20), (-4, 20)]
            pts = [(gx + f[0] * a + r[0] * b, gy + f[1] * a + r[1] * b) for a, b in box]
            pygame.draw.lines(surf, (236, 236, 236), False, pts, 3)
        for (px, py, ph) in track.pads:   # boost pads
            f = (math.cos(ph), math.sin(ph))
            r = (-math.sin(ph), math.cos(ph))
            base = [(-36, -28), (36, -28), (36, 28), (-36, 28)]
            pts = [(px + f[0] * a + r[0] * b, py + f[1] * a + r[1] * b) for a, b in base]
            pygame.draw.polygon(surf, (255, 128, 10), pts)
            pygame.draw.polygon(surf, (255, 220, 80), pts, 3)
            for k in (-18, 2, 22):
                chev = [(k - 12, -18), (k, 0), (k - 12, 18)]
                cp = [(px + f[0] * a + r[0] * b, py + f[1] * a + r[1] * b) for a, b in chev]
                pygame.draw.lines(surf, (255, 250, 200), False, cp, 6)
        for _ in range(60):   # skid marks on the asphalt
            j = prng.randrange(len(dense))
            x, y, s = dense[j]
            nx, ny, _ = dense[(j + 1) % len(dense)]
            a = math.atan2(ny - y, nx - x)
            off = prng.uniform(-half * 0.6, half * 0.6)
            sx, sy = x - math.sin(a) * off, y + math.cos(a) * off
            L = prng.uniform(30, 90)
            c = tuple(int(v * 0.72) for v in th["road"])
            for d in (-5, 5):
                p1 = (sx - math.sin(a) * d, sy + math.cos(a) * d)
                p2 = (p1[0] + math.cos(a) * L, p1[1] + math.sin(a) * L)
                pygame.draw.line(surf, c, p1, p2, 3)

        self.surface = surf
        self.flat = pygame.surfarray.array2d(surf).ravel().copy()   # mapped pixel ints, index = x*S + y
        self.mips = []
        for size in (S // 2, S // 4):
            m = pygame.transform.smoothscale(surf, (size, size))
            m = m.convert(surf) if pygame.display.get_surface() else m
            self.mips.append((size, pygame.surfarray.array2d(m).ravel().copy()))
        self.out_color = surf.map_rgb(th["out"])
        self._make_minimap()
        self._make_props()

    def _make_props(self):
        """Trackside objects along both edges (tyre walls / cones / bollards) + finish gantry."""
        t = self.track
        props = []
        step = 70.0
        n = int(t.length / step)
        for i in range(n):
            frac = i / n
            for side in (-1, 1):
                lat = side * (t.half + 62)
                x, y, _h = t.point_at(frac, lat)
                if t.nearest(x, y)[2] < t.half + 40:   # would sit on another part of the track
                    continue
                props.append((x, y, (i + (side > 0)) % 2))
        self.props = props
        fx, fy, fh = t.point_at(0.0)
        self.gantry = (fx, fy)

    def _make_minimap(self, size=170):
        t = self.track
        mm = pygame.Surface((size, size), pygame.SRCALPHA)
        sc = (size - 16) / MAP_SIZE
        pts = [(8 + x * sc, 8 + y * sc) for x, y in t.pts]
        pygame.draw.rect(mm, (0, 0, 0, 110), mm.get_rect(), border_radius=14)
        pygame.draw.lines(mm, (20, 20, 28), True, pts, max(3, int(t.width * sc) + 3))
        pygame.draw.lines(mm, (226, 226, 236), True, pts, max(2, int(t.width * sc) - 1))
        fx, fy, _ = t.point_at(0)
        pygame.draw.circle(mm, (255, 255, 255), (int(8 + fx * sc), int(8 + fy * sc)), 4)
        self.minimap = mm
        self.mm_scale = sc


class Mode7View:
    def __init__(self, w, h, art, fmt_surface):
        self.w, self.h = w, h
        self.art = art
        self.fmt = fmt_surface
        wide = w / h > 2.5            # split-screen viewport
        self.horizon = int(h * (0.30 if wide else 0.36))
        self.base_focal = (w / 2) / math.tan(math.radians(38 if wide else 36))
        self.cam_back = 160.0
        self.cam_h = (h * 0.80 - self.horizon) * self.cam_back / self.base_focal
        self.rows = np.arange(self.horizon + 1, h, dtype=np.float32)
        self.R = len(self.rows)
        self.cols = np.arange(w, dtype=np.float32) - w / 2
        self.floor = pygame.Surface((w, self.R), 0, fmt_surface)
        self._fog_cache = {}
        self.focal = None
        self.set_focal(self.base_focal)
        self._make_sky(art.theme)

    def set_focal(self, focal):
        """Dynamic field of view (wider at high speed). Recomputes per-row distances."""
        focal = round(focal / 4) * 4   # quantise so fog/rows are cached
        if focal == self.focal:
            return
        self.focal = focal
        self.rowz = (self.cam_h * focal / (self.rows - self.horizon)).astype(np.float32)
        self.colk = (self.cols / focal).astype(np.float32)
        foot = self.rowz / focal     # world units covered by one pixel on that row
        self.cut_mip2 = int(np.sum(foot > 3.4))
        self.cut_mip1 = int(np.sum(foot > 1.7))
        fog = self._fog_cache.get(focal)
        if fog is None:
            th = self.art.theme
            rows = max(1, int(np.sum(self.rowz > 650)))   # only the far rows are fogged -> small overlay
            fog = pygame.Surface((self.w, rows), pygame.SRCALPHA)
            for i in range(rows):
                a = int(max(0.0, min(1.0, (self.rowz[i] - 650) / 1500.0)) ** 0.8 * 235)
                pygame.draw.line(fog, (*th["fog"], a), (0, i), (self.w, i))
            self._fog_cache[focal] = fog
        self.fog = fog

    def _make_sky(self, th):
        fov = 2 * math.atan((self.w / 2) / self.base_focal)
        self.pano_w = int(self.w * 2 * math.pi / fov)
        hz = self.horizon + 1
        sky = pygame.Surface((self.w, hz))
        for y in range(hz):
            f = (y / max(1, hz - 1)) ** 1.4
            c = [int(th["sky1"][i] * (1 - f) + th["sky2"][i] * f) for i in range(3)]
            pygame.draw.line(sky, c, (0, y), (self.w, y))
        self.sky = sky
        rng = random.Random(self.art.track.key + "sky")
        # sun glow
        g = pygame.Surface((int(hz * 1.6), int(hz * 1.6)), pygame.SRCALPHA)
        R = g.get_width() // 2
        for r in range(R, 0, -2):
            a = int(255 * (1 - r / R) ** 2.2)
            pygame.draw.circle(g, (*th["sun"], a), (R, R), r)
        pygame.draw.circle(g, (*th["sun"], 255), (R, R), max(4, R // 9))
        self.sun = g
        self.sun_angle = rng.uniform(-math.pi, math.pi)
        # clouds (soft blobs, drawn small then smooth-scaled up for softness)
        cw, ch = self.pano_w // 2, hz // 2 + 1
        cl = pygame.Surface((cw, ch), pygame.SRCALPHA)
        for _ in range(22):
            cx = rng.uniform(0, cw)
            cy = rng.uniform(ch * 0.12, ch * 0.55)
            spread = rng.uniform(18, 40)
            for _k in range(9):
                rr = rng.uniform(5, 13)
                pygame.draw.ellipse(cl, (*th["cloud"], rng.randint(35, 70)),
                                    pygame.Rect(int(cx + rng.uniform(-spread, spread)), int(cy + rng.uniform(-3, 3)),
                                                int(rr * 2.6), int(rr)))
        for _ in range(2):   # blur: shrink hard, scale back up smoothly
            cl = pygame.transform.smoothscale(cl, (max(1, cw // 8), max(1, ch // 8)))
            cl = pygame.transform.smoothscale(cl, (cw, ch))
        self.clouds = pygame.transform.smoothscale(cl, (self.pano_w, hz))
        # mountain layers: far (hazy) + near (darker), smooth random-walk ridgelines
        layers = []
        for col, amp, rough in ((th["far"], 0.62, 9.0), (th["near"], 0.34, 5.0)):
            pano = pygame.Surface((self.pano_w, hz), pygame.SRCALPHA)
            if self.art.track.theme == "neon" and col == th["near"]:
                x = 0
                while x < self.pano_w:
                    bw = rng.randint(12, 34)
                    bh = rng.randint(int(hz * 0.12), int(hz * 0.55))
                    pygame.draw.rect(pano, col, (x, hz - bh, bw, bh))
                    for wy in range(hz - bh + 4, hz - 3, 6):
                        for wx in range(x + 3, x + bw - 2, 5):
                            if rng.random() < 0.3:
                                pano.fill((255, 210, 120) if rng.random() < 0.8 else (90, 220, 255), (wx, wy, 2, 2))
                    x += bw + rng.randint(0, 4)
            else:
                pts = [(0, hz)]
                y = hz * (1 - amp * 0.6)
                vel = 0.0
                for x in range(0, self.pano_w + 6, 6):
                    vel = vel * 0.85 + rng.uniform(-rough, rough) * 0.35
                    y = max(hz * (1 - amp), min(hz - 3, y + vel))
                    pts.append((x, y))
                pts[-1] = (pts[-1][0], pts[1][1])   # seamless wrap
                pts.append((self.pano_w, hz))
                pygame.draw.polygon(pano, col, pts)
            if not (self.art.track.theme == "neon" and col == th["near"]):
                # aerial perspective: ridge tops fade towards the haze colour; keep the layer's alpha
                grad = pygame.Surface((self.pano_w, hz), pygame.SRCALPHA)
                for yy in range(hz):
                    f = 0.45 * (1 - yy / hz) ** 1.5
                    c = [int(col[i] * (1 - f) + th["fog"][i] * f) for i in range(3)]
                    pygame.draw.line(grad, (*c, 255), (0, yy), (self.pano_w, yy))
                pygame.surfarray.pixels_alpha(grad)[:] = pygame.surfarray.pixels_alpha(pano)
                pano = grad
            if self.art.track.theme == "grass" and col == th["near"]:   # tree line along the horizon
                for x in range(0, self.pano_w, 6):
                    base_y = hz - rng.randint(0, 3)
                    hh = rng.randint(4, 11)
                    pygame.draw.polygon(pano, (38, 82, 52), [(x, base_y), (x + 4, base_y - hh), (x + 8, base_y)])
            layers.append(pano)
        # compose everything into ONE opaque panorama: 2 cheap blits per frame instead of ~9 alpha blits
        full = pygame.Surface((self.pano_w, hz))
        full.blit(pygame.transform.scale(sky, (self.pano_w, hz)), (0, 0))
        sun_x = int((self.sun_angle / (2 * math.pi)) * self.pano_w + self.w / 2) % self.pano_w
        for dx in (-self.pano_w, 0, self.pano_w):
            full.blit(g, g.get_rect(center=(sun_x + dx, int(self.horizon * 0.38))))
        full.blit(self.clouds, (0, 0))
        for layer in layers:
            full.blit(layer, (0, 0))
        self.skypano = full.convert() if pygame.display.get_surface() else full

    def _gather(self, flat, size, wx, wy, scale):
        ix = (wx * scale).astype(np.int32)
        iy = (wy * scale).astype(np.int32)
        oob = (ix < 0) | (ix >= size) | (iy < 0) | (iy >= size)
        np.clip(ix, 0, size - 1, out=ix)
        np.clip(iy, 0, size - 1, out=iy)
        pix = flat[ix * size + iy]
        pix[oob] = self.art.out_color
        return pix

    def render(self, surf, cx, cy, ca, t=0.0):
        fx, fy = math.cos(ca), math.sin(ca)
        rx, ry = -fy, fx
        Z = self.rowz[None, :]
        L = self.colk[:, None] * Z
        wx = cx + fx * Z + rx * L
        wy = cy + fy * Z + ry * L
        S = MAP_SIZE
        c2, c1 = self.cut_mip2, self.cut_mip1
        pix = np.empty((self.w, self.R), dtype=self.art.flat.dtype)
        (s1, f1), (s2, f2) = self.art.mips
        if c2:
            pix[:, :c2] = self._gather(f2, s2, wx[:, :c2], wy[:, :c2], 0.25)
        if c1 > c2:
            pix[:, c2:c1] = self._gather(f1, s1, wx[:, c2:c1], wy[:, c2:c1], 0.5)
        pix[:, c1:] = self._gather(self.art.flat, S, wx[:, c1:], wy[:, c1:], 1.0)
        pygame.surfarray.blit_array(self.floor, pix)
        # sky panorama (gradient + sun + clouds + mountains pre-composed), scrolls with the heading
        off = int((ca / (2 * math.pi)) * self.pano_w) % self.pano_w
        surf.blit(self.skypano, (-off, 0))
        if self.pano_w - off < self.w:
            surf.blit(self.skypano, (self.pano_w - off, 0))
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
        if sx < -300 or sx > self.w + 300:
            return None
        return sx, self.horizon + self.cam_h * k, k, z
