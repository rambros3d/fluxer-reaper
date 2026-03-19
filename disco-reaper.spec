# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

print(f"DEBUG: os.name = {os.name}")
print(f"DEBUG: sys.platform = {sys.platform}")
print(f"DEBUG: Current directory = {os.getcwd()}")
print(f"DEBUG: Files in current directory = {os.listdir('.')}")

hiddenimports = []
hiddenimports += collect_submodules('rich._unicode_data')

# Determine the best icon to use
icon_file = 'disco-reaper-icon.ico' if os.path.exists('disco-reaper-icon.ico') else None

print(f"DEBUG: Selected icon_file = {icon_file}")

a = Analysis(
    ['disco-reaper.py'],
    pathex=[],
    binaries=[],
    datas=[('src/first-info.md', 'src')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DiscoReaper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
