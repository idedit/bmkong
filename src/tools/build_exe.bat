@echo off
REM Build out\BMKONG.exe (needs: pip install -r requirements.txt pyinstaller pillow)
cd /d "%~dp0\.."
python tools\make_icon.py || exit /b 1
python -m PyInstaller BMKONG.spec --noconfirm --clean --distpath ..\out --workpath ..\out\build || exit /b 1
echo Built out\BMKONG.exe
