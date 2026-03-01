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
icon_file = None
if os.path.exists('server-shuttle-icon.ico'):
    icon_file = 'server-shuttle-icon.ico'
elif os.path.exists('server-shuttle-icon.png'):
    icon_file = 'server-shuttle-icon.png'

print(f"DEBUG: Selected icon_file = {icon_file}")

a = Analysis(
    ['server-shuttle.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    name='server-shuttle',
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
