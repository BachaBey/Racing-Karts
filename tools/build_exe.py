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
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        os.path.join(ROOT, "run_client.py"),
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    exe = os.path.join(ROOT, "release", "TurboKartArena.exe")
    print(f"\nBuilt {exe}  ({os.path.getsize(exe) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
