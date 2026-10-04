# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('easytier-core.exe', '.'), ('easytier-cli.exe', '.'), ('Packet.dll', '.'), ('WinDivert64.sys', '.'), ('wintun.dll', '.')],
    hiddenimports=['sqlite3', 'core', 'core.config', 'core.protocol', 'core.event_bus', 'network', 'network.easytier_manager', 'network.peer_manager', 'network.discovery', 'network.crypto', 'network.control_channel', 'features', 'features.chat_manager', 'storage', 'storage.database', 'runtime', 'runtime.async_runtime', 'gui', 'gui.main_window'],
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
    [],
    exclude_binaries=True,
    name='MajesticLink',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MajesticLink',
)
