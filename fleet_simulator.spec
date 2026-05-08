# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CDI Fleet Simulator.

Bundles run_fleet.py + fleet_sim/ + proto stubs + HTML templates
into a single-folder distribution (--onedir) so that
devices_config.yaml and certs stay external and user-editable.

Build:
    pyinstaller fleet_simulator.spec

Output:
    dist/fleet_simulator/
        fleet_simulator.exe
        devices_config.yaml   (copy manually or via post-build)
"""

import os
import sys

block_cipher = None

# ── Paths ────────────────────────────────────────────────────────────────
SPEC_DIR = os.path.abspath(SPECPATH)
PROTO_DIR = SPEC_DIR
TEMPLATES_DIR = os.path.join(SPEC_DIR, 'fleet_sim', 'templates')

# ── Extra data files ─────────────────────────────────────────────────────
# (source_path, dest_folder_inside_bundle)
datas = [
    # HTML templates → fleet_sim/templates/
    (os.path.join(TEMPLATES_DIR, 'control_panel.html'), os.path.join('fleet_sim', 'templates')),
    # Proto stubs → top-level (run_fleet.py adds proto dir to sys.path)
    (os.path.join(PROTO_DIR, 'telemetry_pb2.py'), '.'),
    (os.path.join(PROTO_DIR, 'telemetry_pb2_grpc.py'), '.'),
]

# ── Hidden imports PyInstaller can miss ──────────────────────────────────
hiddenimports = [
    'telemetry_pb2',
    'telemetry_pb2_grpc',
    'grpc',
    'grpc._cython',
    'grpc._cython.cygrpc',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'starlette',
    'starlette.responses',
    'starlette.routing',
    'starlette.templating',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'yaml',
    'cryptography',
    'json',
    'google.protobuf',
    'google.protobuf.descriptor',
    'google.protobuf.descriptor_pool',
    'google.protobuf.runtime_version',
    'google.protobuf.symbol_database',
    'google._upb._message',
]

# ── Analysis ─────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(SPEC_DIR, 'run_fleet.py')],
    pathex=[SPEC_DIR, PROTO_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='fleet_simulator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # keep console for logs
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='fleet_simulator',
)
