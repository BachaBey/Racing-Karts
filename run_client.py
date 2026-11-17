"""Start the Turbo Kart Arena client.

    python run_client.py                       # then type the server IP in the login screen
    python run_client.py --host 100.64.0.5     # pre-fill the server address
    python run_client.py --selftest            # check that rendering + race code load (used by the exe build)
"""
import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def selftest():
    """Load every race-time module and render one Mode-7 frame with karts, offscreen.
    Writes 'OK ...' or the traceback to selftest.log (next to the exe when packaged)."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    log = os.path.join(base, "selftest.log")
    try:
        import pygame
        pygame.init()
        pygame.display.set_mode((64, 64), pygame.HIDDEN)
        import client.race  # noqa: F401  (pulls numpy, mode7, sprites, kartmodel)
        from client.render.mode7 import Mode7View, TrackArt
        from client.render.sprites import SpriteBank
        from common.track import TRACK_ORDER, Track
        sb = SpriteBank()
        sb.prewarm([0, 1])
        for key in TRACK_ORDER:
            art = TrackArt(Track(key))
            view = Mode7View(640, 360, art, art.surface)
            surf = pygame.Surface((640, 360)).convert()
            x, y, a = art.track.grid_slot(0)
            view.render(surf, x, y, a)
            surf.blit(sb.kart(0, 0.3), (300, 250))
            surf.blit(sb.kart(1, 2.0, ghost=True), (200, 250))
        pygame.quit()
        with open(log, "w", encoding="utf-8") as f:
            f.write("OK - numpy, Mode-7 renderer, 3D kart sprites and all tracks work\n")
        return 0
    except Exception:
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        return 1


def main():
    ap = argparse.ArgumentParser(description="Turbo Kart Arena client")
    ap.add_argument("--host", default=None, help="server address to pre-fill")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    from client.app import App
    App(args.host, args.fullscreen).run()


if __name__ == "__main__":
    main()
