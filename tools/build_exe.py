"""Build the stand-alone Windows client:  release/TurboKartArena.exe

    pip install pyinstaller
    python tools/build_exe.py

Players only need the .exe (no Python, no pip). Settings are saved next to it.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",            # single file to share
        "--windowed",           # no console window
        "--name", "TurboKartArena",
        "--distpath", os.path.join(ROOT, "release"),
        "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
        "--specpath", os.path.join(ROOT, "build"),
        "--paths", ROOT,
        "--exclude-module", "tkinter",
        # numpy imports these from compiled (Cython) code, which PyInstaller cannot see
        "--hidden-import", "secrets",
        "--hidden-import", "hmac",
        # game modules that are imported lazily
        "--hidden-import", "client.race",
        "--hidden-import", "client.render.mode7",
        "--hidden-import", "client.render.kartmodel",
        os.path.join(ROOT, "run_client.py"),
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    exe = os.path.join(ROOT, "release", "TurboKartArena.exe")
    print(f"\nBuilt {exe}  ({os.path.getsize(exe) / 1e6:.1f} MB)")

    # run the packaged exe's self-test: imports numpy + renders a race frame offscreen
    log = os.path.join(ROOT, "release", "selftest.log")
    if os.path.exists(log):
        os.remove(log)
    subprocess.run([exe, "--selftest"], timeout=120)
    result = open(log, encoding="utf-8").read() if os.path.exists(log) else "no selftest.log written"
    os.remove(log) if os.path.exists(log) else None
    print("self-test:", result.strip())
    if not result.startswith("OK"):
        sys.exit("SELF-TEST FAILED - do not publish this exe")


if __name__ == "__main__":
    main()
