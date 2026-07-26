# -*- mode: python ; coding: utf-8 -*-

import glob
import os
import sys


def collect_python_runtime_binaries():
    binaries = []
    search_dirs = {
        os.path.dirname(sys.executable),
        sys.base_prefix,
        os.path.join(sys.base_prefix, "DLLs"),
    }
    patterns = [
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "vcruntime*.dll",
        "api-ms-win-*.dll",
    ]
    seen = set()
    for folder in search_dirs:
        if not folder or not os.path.isdir(folder):
            continue
        for pattern in patterns:
            for path in glob.glob(os.path.join(folder, pattern)):
                norm = os.path.normcase(os.path.abspath(path))
                if os.path.isfile(path) and norm not in seen:
                    binaries.append((path, "."))
                    seen.add(norm)
    return binaries


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=collect_python_runtime_binaries(),
    datas=[('templates', 'templates'), ('static', 'static'), ('authencation', 'authencation'), ('.env', '.'), ('VERSION', '.')],
    hiddenimports=[
        'appdirs', 'tkinter', 'tkinter.scrolledtext', 'websocket',
        'unicodedata', 'encodings', 'encodings.utf_8', 'codecs',
        'core.zalo.enc', 'core.zalo.dec', 'core.zalo.debugs',
        'core.zalo.zalo_config', 'core.zalo.zalo_headers',
        'features.accounts.account_manager',
        'features.accounts.account_network_monitor',
        'features.accounts.get_loginInfo',
        'features.accounts.profile_me_v2',
        'features.groups.add_group',
        'features.groups.get_group',
        'features.groups.group_manager',
        'features.groups.group_copy_manager',
        'features.groups.group_copy_worker',
        'features.groups.invite_group',
        'features.groups.send_sms_group',
        'features.members.get_members',
        'features.members.group_member_service',
        'features.messaging.add_friend',
        'features.messaging.send_sms',
        'features.messages.message_manager',
        'features.profiles.fetch_userinfo',
        'features.profiles.get_avata',
        'features.profiles.get_info',
        'features.profiles.get_single_profile',
        'features.profiles.profile_service',
        'features.profiles.search_info_from_phone',
        'features.schedules.schedule_manager',
        'features.schedules.schedule_worker',
        'features.tasks.task_manager',
        'authencation.send_info_device', 'cryptography.fernet', 'dotenv'
    ],
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
    a.datas,
    [],
    name='Nexus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/ico/app.ico',
)
