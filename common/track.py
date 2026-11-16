"""Track geometry shared by server and client (pure Python, no pygame).

A track is a closed Catmull-Rom spline through control points.  The spline is
sampled into a dense centerline; everything else (on-road test, race progress,
lap counting, grid slots, item boxes) is computed from that centerline so the
server and every client agree on the exact same geometry.
"""
import math
import random

MAP_SIZE = 2048

TRACKS = {
    "sunny": {
        "title": "Sunny Circuit",
        "theme": "grass",
        "width": 170,
        "points": [(300, 1700), (300, 600), (450, 350), (750, 300), (950, 450), (1000, 750),
                   (1150, 900), (1450, 850), (1700, 600), (1850, 800), (1800, 1300),
                   (1600, 1500), (1300, 1450), (1050, 1600), (900, 1800), (550, 1850)],
        "boxes": [0.22, 0.52, 0.78],
        "pads": [0.10, 0.62],
    },
    "desert": {
        "title": "Dune Raceway",
        "theme": "desert",
        "width": 160,
        "points": [(400, 1000), (450, 500), (800, 250), (1200, 350), (1300, 700), (1050, 950),
                   (1100, 1200), (1500, 1150), (1750, 850), (1830, 1400), (1500, 1800),
                   (900, 1820), (500, 1550)],
        "boxes": [0.18, 0.47, 0.75],
        "pads": [0.33, 0.85],
    },
    "neon": {
        "title": "Neon City",
        "theme": "neon",
        "width": 160,
        "points": [(250, 250), (1000, 220), (1800, 300), (1800, 800), (1300, 800), (1250, 1100),
                   (1800, 1150), (1800, 1800), (1100, 1800), (1000, 1350), (650, 1300),
                   (600, 1800), (250, 1800)],
        "boxes": [0.15, 0.45, 0.72],
        "pads": [0.06, 0.58],
    },
}
TRACK_ORDER = ["sunny", "desert", "neon"]


def _catmull(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t
    return tuple(0.5 * ((2 * p1[i]) + (-p0[i] + p2[i]) * t + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * t2
                        + (-p0[i] + 3 * p1[i] - 3 * p2[i] + p3[i]) * t3) for i in range(2))


def wrap_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Track:
    def __init__(self, key):
        if key not in TRACKS:
            key = TRACK_ORDER[0]
        d = TRACKS[key]
        self.key = key
        self.title = d["title"]
        self.theme = d["theme"]
        self.width = d["width"]
        self.half = d["width"] / 2.0
        ctrl = d["points"]
        n = len(ctrl)
        raw = []
        for i in range(n):
            p0, p1, p2, p3 = ctrl[(i - 1) % n], ctrl[i], ctrl[(i + 1) % n], ctrl[(i + 2) % n]
            for k in range(24):
                raw.append(_catmull(p0, p1, p2, p3, k / 24.0))
        # resample to (roughly) equal spacing so progress is proportional to distance
        self.pts = self._resample(raw, 24.0)
        self.n = len(self.pts)
        self.cum = [0.0]
        self.seg_len = []
        for i in range(self.n):
            a, b = self.pts[i], self.pts[(i + 1) % self.n]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            self.seg_len.append(L)
            self.cum.append(self.cum[-1] + L)
        self.length = self.cum[-1]
        self.box_positions = []
        for frac in d["boxes"]:
            for lat in (-0.32, -0.11, 0.11, 0.32):
                x, y, _ = self.point_at(frac, lat * self.width)
                self.box_positions.append((x, y))
        self.pads = []
        for frac in d["pads"]:
            x, y, a = self.point_at(frac, 0.0)
            self.pads.append((x, y, a))

    @staticmethod
    def _resample(raw, spacing):
        pts = [raw[0]]
        carry = 0.0
        m = len(raw)
        for i in range(m):
            a, b = raw[i], raw[(i + 1) % m]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L == 0:
                continue
            pos = spacing - carry
            while pos <= L:
                t = pos / L
                pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                pos += spacing
            carry = L - (pos - spacing)
        if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < spacing * 0.5:
            pts.pop()
        return pts

    # ------------------------------------------------------------------ queries
    def _seg_project(self, i, x, y):
        ax, ay = self.pts[i]
        bx, by = self.pts[(i + 1) % self.n]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy or 1e-9
        t = ((x - ax) * dx + (y - ay) * dy) / L2
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        px, py = ax + dx * t, ay + dy * t
        return (px - x) ** 2 + (py - y) ** 2, t

    def nearest(self, x, y, hint=None):
        """Returns (segment_index, t, distance). Searches around `hint` first (fast, avoids
        snapping to a different part of the track that happens to be close)."""
        best = (1e18, 0, 0.0)
        if hint is not None:
            for k in range(-10, 14):
                i = (hint + k) % self.n
                d2, t = self._seg_project(i, x, y)
                if d2 < best[0]:
                    best = (d2, i, t)
            if best[0] < (self.width * 1.6) ** 2:
                return best[1], best[2], math.sqrt(best[0])
            best = (1e18, 0, 0.0)
        for i in range(self.n):
            d2, t = self._seg_project(i, x, y)
            if d2 < best[0]:
                best = (d2, i, t)
        return best[1], best[2], math.sqrt(best[0])

    def progress(self, x, y, hint=None):
        """Fraction of a lap [0,1) of the closest centerline point + segment index + distance."""
        i, t, d = self.nearest(x, y, hint)
        s = self.cum[i] + self.seg_len[i] * t
        return s / self.length, i, d

    def on_road(self, x, y, hint=None):
        i, t, d = self.nearest(x, y, hint)
        return d <= self.half, i

    def point_at(self, frac, lateral=0.0):
        """World position at lap fraction `frac` shifted `lateral` units to the right. Returns (x, y, heading)."""
        s = (frac % 1.0) * self.length
        lo, hi = 0, self.n
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        i = lo
        t = (s - self.cum[i]) / (self.seg_len[i] or 1)
        ax, ay = self.pts[i]
        bx, by = self.pts[(i + 1) % self.n]
        heading = math.atan2(by - ay, bx - ax)
        x = ax + (bx - ax) * t - math.sin(heading) * lateral
        y = ay + (by - ay) * t + math.cos(heading) * lateral
        return x, y, heading

    def grid_slot(self, slot):
        """Starting position for grid slot (0..n): two columns, rows behind the finish line."""
        row, col = divmod(slot, 2)
        back = (60 + row * 55 + col * 22) / self.length
        lat = (-0.22 if col == 0 else 0.22) * self.width
        return self.point_at(-back, lat)

    def scenery(self, count=170):
        """Deterministic decoration positions off the road (same on every client)."""
        rng = random.Random(self.key)
        out = []
        tries = 0
        while len(out) < count and tries < count * 40:
            tries += 1
            x, y = rng.uniform(40, MAP_SIZE - 40), rng.uniform(40, MAP_SIZE - 40)
            _, _, d = self.nearest(x, y)
            if d > self.half + 60:
                out.append((x, y, rng.random()))
        return out
