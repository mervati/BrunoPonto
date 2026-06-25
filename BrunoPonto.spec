# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['bruno_ponto.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['selenium.webdriver.chrome.webdriver', 'selenium.webdriver.chrome.service', 'selenium.webdriver.chrome.options', 'selenium.webdriver.edge.webdriver', 'selenium.webdriver.edge.service', 'selenium.webdriver.edge.options', 'selenium.webdriver.firefox.webdriver', 'selenium.webdriver.firefox.service', 'selenium.webdriver.firefox.options', 'selenium.webdriver.remote.webdriver', 'selenium.webdriver.support.ui', 'selenium.webdriver.support.expected_conditions', 'selenium.webdriver.common.by', 'selenium.webdriver.common.action_chains', 'selenium.webdriver.common.keys', 'selenium.common.exceptions'],
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
    name='BrunoPonto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
