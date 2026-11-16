"""Low-poly 3D kart + driver model and a small software renderer that turns it into sprites.

Model space: x = forward, y = right, z = up (1 unit = 1 world unit; the kart is ~44 long).
Renderer: orthographic camera above/behind, per-face Lambert + Blinn-style specular lighting,
painter's algorithm (faces sorted back-to-front), 3x supersampling, cartoon outline.
"""
import math

import pygame

# ---------------------------------------------------------------- materials
def mat(color, spec=0.0, shine=12.0, amb=0.42):
    return {"color": color, "spec": spec, "shine": shine, "amb": amb}


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------- geometry helpers
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    L = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / L, a[1] / L, a[2] / L)


def _centroid(pts):
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


def _faces_outward(faces, center):
    """Give every face an outward normal (convex primitives: face centroid - shape center)."""
    out = []
    for pts, m in faces:
        n = _norm(_cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0])))
        if _dot(n, _sub(_centroid(pts), center)) < 0:
            n = (-n[0], -n[1], -n[2])
        out.append((pts, n, m))
    return out


def wedge(x0, x1, bw, fw, bz0, bz1, fz0, fz1, m, yc=0.0):
    """Hexahedron from a back section (x0, half-width bw, z bz0..bz1) to a front section (x1, fw, fz0..fz1).
    Covers boxes, tapered nose cones, sloped panels, wings..."""
    b = [(x0, yc - bw, bz0), (x0, yc + bw, bz0), (x0, yc + bw, bz1), (x0, yc - bw, bz1)]
    f = [(x1, yc - fw, fz0), (x1, yc + fw, fz0), (x1, yc + fw, fz1), (x1, yc - fw, fz1)]
    faces = [(b, m), (f, m),
             ([b[0], f[0], f[3], b[3]], m), ([b[1], f[1], f[2], b[2]], m),
             ([b[0], b[1], f[1], f[0]], m), ([b[3], b[2], f[2], f[3]], m)]
    return _faces_outward(faces, _centroid(b + f))


def cylinder(c, axis, r, half, m_side, m_cap=None, seg=14):
    m_cap = m_cap or m_side
    ring0, ring1 = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        u, v = math.cos(a) * r, math.sin(a) * r
        if axis == "y":
            ring0.append((c[0] + u, c[1] - half, c[2] + v))
            ring1.append((c[0] + u, c[1] + half, c[2] + v))
        elif axis == "x":
            ring0.append((c[0] - half, c[1] + u, c[2] + v))
            ring1.append((c[0] + half, c[1] + u, c[2] + v))
        else:
            ring0.append((c[0] + u, c[1] + v, c[2] - half))
            ring1.append((c[0] + u, c[1] + v, c[2] + half))
    faces = [(ring0, m_cap), (ring1, m_cap)]
    for i in range(seg):
        j = (i + 1) % seg
        faces.append(([ring0[i], ring0[j], ring1[j], ring1[i]], m_side))
    return _faces_outward(faces, c)


def ellipsoid(c, r, m, lon=14, lat=9):
    faces = []
    for i in range(lat):
        t0, t1 = math.pi * i / lat - math.pi / 2, math.pi * (i + 1) / lat - math.pi / 2
        for j in range(lon):
            p0, p1 = 2 * math.pi * j / lon, 2 * math.pi * (j + 1) / lon
            def pt(t, p):
                return (c[0] + r[0] * math.cos(t) * math.cos(p), c[1] + r[1] * math.cos(t) * math.sin(p),
                        c[2] + r[2] * math.sin(t))
            q = [pt(t0, p0), pt(t0, p1), pt(t1, p1), pt(t1, p0)]
            if i == 0:
                q = [q[0], q[2], q[3]]
            elif i == lat - 1:
                q = [q[0], q[1], q[2]]
            faces.append((q, m))
    return _faces_outward(faces, c)


# ---------------------------------------------------------------- the kart
def kart_mesh(color):
    """Player-coloured kart with driver. Returns (faces, exhaust tip points)."""
    body = mat(color, spec=0.55, shine=18)
    body_dark = mat(_shade(color, 0.62), spec=0.3)
    suit = mat(_mix(color, (255, 255, 255), 0.25), spec=0.1)
    helmet = mat(_shade(color, 1.05), spec=1.0, shine=28)
    visor = mat((25, 35, 60), spec=1.2, shine=40, amb=0.35)
    white = mat((245, 245, 248), spec=0.4)
    carbon = mat((38, 40, 46), spec=0.25)
    tire = mat((24, 24, 28), spec=0.08, amb=0.5)
    tire_wall = mat((44, 44, 50), spec=0.1, amb=0.5)
    rim = mat((200, 205, 215), spec=1.0, shine=30)
    metal = mat((120, 125, 135), spec=0.8, shine=22)
    chrome = mat((215, 220, 230), spec=1.3, shine=40)
    glove = mat((40, 40, 46), spec=0.2)

    F = []
    # chassis + bodywork
    F += wedge(-18, 18, 9.5, 9.5, 1.8, 3.6, 1.8, 3.4, carbon)                    # floor pan
    F += wedge(-11, 8, 8.2, 7.2, 3.2, 9.5, 3.2, 8.4, body)                       # main tub
    F += wedge(8, 22, 7.2, 3.4, 3.2, 8.4, 2.6, 5.2, body)                        # nose cone
    F += wedge(8.6, 21.4, 2.0, 1.1, 8.4, 8.9, 5.2, 5.7, white)                   # racing stripe
    F += wedge(19.5, 24.5, 13.0, 12.0, 1.6, 3.4, 1.6, 2.6, body_dark)            # front wing
    for s in (-1, 1):
        F += wedge(19.5, 24.5, 0.6, 0.6, 1.6, 4.8, 1.6, 3.6, carbon, yc=s * 12.6)  # wing endplates
        F += wedge(-10, 6, 2.6, 2.2, 3.2, 7.4, 3.2, 6.2, body_dark, yc=s * 9.8)   # side pods
        F += wedge(-9.5, 5.5, 0.4, 0.4, 5.6, 6.6, 5.0, 5.8, white, yc=s * 12.45)  # pod stripe
    # engine + exhausts
    F += wedge(-20, -10.5, 6.2, 6.0, 3.4, 9.0, 3.4, 9.0, metal)                  # engine block
    F += wedge(-19.6, -10.5, 6.6, 6.4, 9.0, 11.0, 9.0, 12.5, body_dark)          # engine cover
    F += wedge(-16, -11.5, 2.2, 2.6, 11.0, 13.4, 12.5, 14.6, carbon)             # air intake
    F += wedge(-19.5, -11, 6.5, 6.3, 5.6, 6.6, 5.6, 6.6, carbon)                 # cooling fins
    lamp = mat((255, 70, 50), spec=0.6, amb=1.15)                                 # self-lit tail lights
    for s in (-1, 1):
        F += wedge(-20.6, -20.0, 1.6, 1.6, 4.4, 6.0, 4.4, 6.0, lamp, yc=s * 4.4)
    exhausts = []
    for s in (-1, 1):
        F += cylinder((-21.5, s * 3.6, 8.6), "x", 1.45, 3.0, chrome, mat((20, 20, 20)), seg=10)
        exhausts.append((-24.6, s * 3.6, 8.6))
    # rear wing
    F += wedge(-24, -18.5, 12.5, 12.5, 16.0, 17.6, 15.2, 16.4, body)
    for s in (-1, 1):
        F += wedge(-24.5, -18, 0.55, 0.55, 12.6, 18.4, 12.6, 17.4, carbon, yc=s * 12.9)
        F += wedge(-21.5, -20, 0.7, 0.7, 9.5, 16.0, 9.5, 15.6, carbon, yc=s * 4.5)
    # wheels (tyre + sidewall caps + chrome rim)
    for wx, wy, r, hw in ((-12.5, 12.8, 6.4, 3.4), (-12.5, -12.8, 6.4, 3.4),
                          (13.0, 11.6, 5.2, 2.8), (13.0, -11.6, 5.2, 2.8)):
        F += cylinder((wx, wy, r), "y", r, hw, tire, tire_wall, seg=16)
        side = 1 if wy > 0 else -1
        F += cylinder((wx, wy + side * (hw + 0.15), r), "y", r * 0.5, 0.3, rim, rim, seg=10)
    # driver
    F += wedge(-12, -8.5, 5.4, 5.0, 8.0, 18.5, 8.0, 17.5, carbon)                # seat back
    F += wedge(-9.5, -3.0, 4.6, 4.0, 8.0, 16.6, 8.0, 15.4, suit)                 # torso
    for s in (-1, 1):
        F += wedge(-6.0, 3.6, 1.3, 1.1, 13.6, 15.6, 10.6, 12.2, suit, yc=s * 4.2)  # arms
        F += ellipsoid((4.0, s * 3.4, 11.4), (1.4, 1.4, 1.4), glove, lon=8, lat=5)  # gloves
    F += cylinder((5.2, 0, 11.6), "x", 3.1, 0.45, carbon, carbon, seg=12)        # steering wheel
    F += wedge(-7.0, -5.2, 1.6, 1.6, 15.2, 16.8, 15.2, 16.8, suit)               # neck
    F += ellipsoid((-5.6, 0, 20.4), (4.9, 4.5, 4.7), helmet)                     # helmet
    F += ellipsoid((-3.6, 0, 20.2), (3.3, 3.7, 2.3), visor, lon=12, lat=7)       # visor
    F += ellipsoid((-5.6, 0, 22.6), (5.0, 1.1, 2.6), white, lon=10, lat=6)       # helmet stripe
    return F, exhausts


# ---------------------------------------------------------------- renderer
PITCH = 0.36                       # camera elevation (radians)
_LIGHT = _norm((-0.55, -0.45, 0.85))  # from behind the viewer, upper-left (in view-aligned model space)


def render(faces, yaw, size, world, ground_y, supersample=3, shadow=True, points=()):
    """Render the mesh rotated by `yaw` (0 = we see the back). Returns (surface, projected points)."""
    S = size * supersample
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    k = S / world
    cy_, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    ox, oy = S / 2, S * ground_y
    view = (cp, 0.0, -sp)
    to_eye = (-cp, 0.0, sp)
    half = _norm((to_eye[0] + _LIGHT[0], to_eye[1] + _LIGHT[1], to_eye[2] + _LIGHT[2]))

    def proj(p):
        X = p[0] * cy_ - p[1] * sy_
        Y = p[0] * sy_ + p[1] * cy_
        return (ox + Y * k, oy - (p[2] * cp + X * sp) * k), X * cp - p[2] * sp

    if shadow:
        sh = pygame.Surface((S, S), pygame.SRCALPHA)
        pts = []
        for i in range(20):
            a = 2 * math.pi * i / 20
            pts.append(proj((math.cos(a) * 24, math.sin(a) * 16, 0))[0])
        pygame.draw.polygon(sh, (0, 0, 0, 95), pts)
        surf.blit(sh, (0, 0))

    polys = []
    for pts, n, m in faces:
        nx = n[0] * cy_ - n[1] * sy_
        ny = n[0] * sy_ + n[1] * cy_
        nz = n[2]
        if nx * view[0] + nz * view[2] >= 0:          # back-face culling
            continue
        scr = []
        depth = 0.0
        for p in pts:
            s2, d = proj(p)
            scr.append(s2)
            depth += d
        nn = (nx, ny, nz)
        diff = max(0.0, _dot(nn, _LIGHT))
        spec = m["spec"] * max(0.0, _dot(nn, half)) ** m["shine"] if m["spec"] else 0.0
        rim_l = 0.18 * max(0.0, 1.0 - abs(_dot(nn, to_eye))) ** 2      # subtle rim light on silhouettes
        c = m["color"]
        lit = m["amb"] + 0.75 * diff + rim_l
        col = tuple(max(0, min(255, int(c[i] * lit + 255 * spec))) for i in range(3))
        polys.append((depth / len(pts), scr, col))
    polys.sort(key=lambda p: -p[0])
    for _d, scr, col in polys:
        pygame.draw.polygon(surf, col, scr)
    img = pygame.transform.smoothscale(surf, (size, size))
    img = outline(img)
    projected = []
    for p in points:
        (sx, sy), d = proj(p)
        projected.append((sx / S, sy / S, d))
    return img, projected


def outline(img, color=(16, 14, 26)):
    """Cartoon outline: dilate the sprite's silhouette by 1 px and draw it behind."""
    mask = pygame.mask.from_surface(img, 140)   # threshold above the shadow's alpha
    sil = mask.to_surface(setcolor=(*color, 255), unsetcolor=(0, 0, 0, 0))
    out = pygame.Surface(img.get_size(), pygame.SRCALPHA)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        out.blit(sil, (dx, dy))
    out.blit(img, (0, 0))
    return out
