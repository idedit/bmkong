# PyInstaller spec: one-file, windowed BMKONG.exe with the Python runtime bundled.
# Build with tools/build_exe.bat, which writes the exe to ../out and scratch files to ../out/build.
import os

here = SPECPATH  # src/

a = Analysis(
    [os.path.join(here, "run.py")],
    pathex=[here],
    datas=[(os.path.join(here, src), dst) for src, dst in [
        ("bmkong/web/index.html", "bmkong/web"),
        ("assets/icon.ico", "assets"),
    ]],
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"],
    excludes=["tkinter", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="BMKONG",
    icon=os.path.join(here, "assets", "icon.ico"),
    console=False,
    upx=False,
)
