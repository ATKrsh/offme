# offme.spec - PyInstaller onefile portable build for OffMe
# Usage: pyinstaller offme.spec
# Output: dist\offme_v1.exe  (single file, no install needed, no console)

block_cipher = None

a = Analysis(
    ['offme.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'ctypes',
        'ctypes.wintypes',
        'threading',
        'subprocess',
        'json',
        'math',
        'time',
        'os',
        'sys',
        'PyQt5.QtWidgets',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.sip',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'PIL', 'cv2',
        'scipy', 'pandas', 'IPython', 'jupyter',
        'PyQt5.QtWebEngine', 'PyQt5.QtMultimedia',
        'PyQt5.QtBluetooth', 'PyQt5.QtNetwork',
        'PyQt5.QtSql', 'PyQt5.QtTest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='offme_v7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can cause false AV positives — disabled
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    uac_admin=False,     # Set True to always request admin (for internet toggle)
)
