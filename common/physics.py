"""Arcade kart physics + a simple AI driver.

Each client simulates ITS OWN kart(s) locally (instant response, no input lag)
and streams the resulting state to the server over UDP.  The server validates
the states (speed bound) and stays authoritative for everything that involves
more than one player: item boxes, projectiles, hits, laps, ranking.
"""
import math

from . import config as C
from .track import MAP_SIZE, wrap_angle


class KartPhysics:
    def __init__(self, x=0.0, y=0.0, angle=0.0):
        self.x, self.y, self.angle = x, y, angle
        self.speed = 0.0
        self.spin_t = 0.0
        self.boost_t = 0.0
        self.hint = None
        self.on_road = True

    @property
    def flags_boost(self):
        return self.boost_t > 0

    def spin(self):
        self.spin_t = C.SPIN_TIME
        self.boost_t = 0.0

    def boost(self, t=C.BOOST_TIME):
        self.boost_t = max(self.boost_t, t)

    def step(self, dt, throttle, brake, steer, track, speed_factor=1.0):
        on_road, self.hint = track.on_road(self.x, self.y, self.hint)
        self.on_road = on_road
        for (px, py, _a) in track.pads:
            if (self.x - px) ** 2 + (self.y - py) ** 2 < 40 ** 2:
                self.boost(0.8)
        if self.boost_t > 0:
            self.boost_t -= dt
            max_spd = C.KART_BOOST_SPEED
        else:
            max_spd = (C.KART_MAX_SPEED if on_road else C.KART_GRASS_SPEED) * speed_factor

        if self.spin_t > 0:
            self.spin_t -= dt
            self.angle = wrap_angle(self.angle + 11.0 * dt)
            self.speed -= self.speed * 2.5 * dt
        else:
            if self.boost_t > 0:
                self.speed = min(self.speed + 900 * dt, max_spd)
            elif throttle:
                if self.speed < max_spd:
                    self.speed = min(max_spd, self.speed + C.KART_ACCEL * dt)
            elif brake:
                if self.speed > 0:
                    self.speed = max(0.0, self.speed - C.KART_BRAKE * dt)
                else:
                    self.speed = max(-C.KART_REVERSE_MAX, self.speed - C.KART_ACCEL * 0.5 * dt)
            else:
                if self.speed > 0:
                    self.speed = max(0.0, self.speed - C.KART_FRICTION * dt)
                else:
                    self.speed = min(0.0, self.speed + C.KART_FRICTION * dt)
            if self.speed > max_spd:  # e.g. drove onto grass: bleed off speed smoothly
                self.speed -= (self.speed - max_spd) * 3.0 * dt
            grip = min(1.0, abs(self.speed) / 90.0)
            direction = 1 if self.speed >= 0 else -1
            self.angle = wrap_angle(self.angle + steer * C.KART_TURN_RATE * grip * direction * dt)

        self.x += math.cos(self.angle) * self.speed * dt
        self.y += math.sin(self.angle) * self.speed * dt
        lo, hi = 20.0, MAP_SIZE - 20.0
        if not (lo <= self.x <= hi and lo <= self.y <= hi):
            self.x = min(hi, max(lo, self.x))
            self.y = min(hi, max(lo, self.y))
            self.speed *= 0.5

    def collide_with(self, ox, oy):
        """Push this kart out of another kart (each client resolves its own kart)."""
        dx, dy = self.x - ox, self.y - oy
        d = math.hypot(dx, dy)
        min_d = C.KART_RADIUS * 2
        if 0 < d < min_d:
            push = (min_d - d)
            self.x += dx / d * push
            self.y += dy / d * push
            self.speed *= 0.85
            return True
        return False


class AIDriver:
    """Steers towards a point further along the centerline. Used by CPU ghosts and bot clients."""

    def __init__(self, track, skill=1.0, lane=0.0):
        self.track = track
        self.skill = skill
        self.lane = lane

    def control(self, kart):
        prog, _i, _d = self.track.progress(kart.x, kart.y, kart.hint)
        look = (70 + abs(kart.speed) * 0.45) / self.track.length
        tx, ty, _ = self.track.point_at(prog + look, self.lane * self.track.width)
        desired = math.atan2(ty - kart.y, tx - kart.x)
        diff = wrap_angle(desired - kart.angle)
        steer = max(-1.0, min(1.0, diff * 2.2))
        throttle = abs(diff) < 1.2 or kart.speed < 60
        brake = abs(diff) > 1.2 and kart.speed > 120
        return throttle, brake, steer
