import os
import sys
from pathlib import Path
import comtypes.client


project_root = Path(SPECPATH).resolve()
package_root = project_root / "src" / "ai_piano_studio"
piano_root = project_root / "resources" / "piano"
# Generate the system UIA typelib at build time so frozen helpers can import
# both friendly and GUID modules without relying on a writable app directory.
# This reads type definitions only; it does not inspect any desktop controls.
_uia = comtypes.client.GetModule(str(Path(os.environ['WINDIR']) / 'System32' / 'UIAutomationCore.dll'))
_uia_hiddenimports = [_uia.__name__, _uia.IUIAutomation.__module__]
datas = [
    (str(project_root / "build" / "release-notices"), "ai_piano_studio/resources/third-party"),
    (str(package_root / "qml"), "ai_piano_studio/qml"),
    (str(package_root / "resources"), "ai_piano_studio/resources"),
    (str(piano_root / "banks.json"), "ai_piano_studio/resources/piano"),
    (str(piano_root / "banks"), "ai_piano_studio/resources/piano/banks"),
    (str(piano_root / "LICENSE-SALAMANDER.txt"), "ai_piano_studio/resources/piano"),
    (str(piano_root / "PROVENANCE-SALAMANDER.json"), "ai_piano_studio/resources/piano"),
    (str(piano_root / "PROVENANCE-SALAMANDER.md"), "ai_piano_studio/resources/piano"),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "ai_piano_studio/resources"),
    (str(project_root / "LICENSE"), "ai_piano_studio/resources"),
    (str(project_root / "COPYRIGHT"), "ai_piano_studio/resources"),
    (str(project_root / "docs" / "LICENSING.md"), "ai_piano_studio/resources"),
]

a = Analysis(
    ["src/ai_piano_studio/studio.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=_uia_hiddenimports,
    hookspath=[str(project_root / "installer" / "hooks")],
    hooksconfig={"flowing_piano": {"qml_root": str(package_root / "qml"),
                                  "qml_report": str(project_root / "build" / "qml-module-inventory.json")}},
    runtime_hooks=[str(project_root / "installer" / "windows_runtime_hook.py")],
    excludes=[],
    noarchive=False,
)
# Optional character artwork belongs to a user's private profile, never a release.
a.datas = type(a.datas)(
    item for item in a.datas
    if not (Path(item[0]).name.startswith("skin-") and Path(item[0]).suffix == ".png")
)


def _unused_browser_component(destination):
    """QML's catch-all hook collects browser/PDF plugins this app never uses."""
    path = destination.replace('\\', '/').lower()
    if not path.startswith('pyside6/'):
        return False
    leaf = path.rsplit('/', 1)[-1]
    return (
        leaf.startswith(('qt6webengine', 'qt6webview', 'qt6pdf', 'qtwebengineprocess'))
        or any('/' + module + '/' in path for module in (
            'qml/qtwebengine', 'qml/qtwebview', 'qml/qtquick/pdf', 'plugins/webview',
        ))
        or (path.startswith('pyside6/resources/') and
            (leaf.startswith(('qtwebengine_', 'v8_context_snapshot')) or leaf == 'icudtl.dat'))
        or '/qtwebengine_locales/' in path
        or path == 'pyside6/plugins/imageformats/qpdf.dll'
        or leaf.startswith(('qtwebenginecore.', 'qtwebenginequick.', 'qtwebenginewidgets.', 'qtpdf.'))
    )


a.datas = type(a.datas)(item for item in a.datas if not _unused_browser_component(item[0]))
a.binaries = type(a.binaries)(item for item in a.binaries if not _unused_browser_component(item[0]))

# A developer PATH can contain a different OpenSSL/SQLite/libffi (for example
# from a separate Python distribution). Bundle the dependencies matching the
# pinned interpreter rather than silently resolving those host DLLs.
_runtime_dll_names = {'libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'sqlite3.dll', 'libffi-8.dll'}
_runtime_dll_root = Path(sys.base_prefix) / 'DLLs'
_runtime_binaries = []
for _destination, _source, _typecode in a.binaries:
    _leaf = Path(_destination).name.lower()
    # Qt 6.12 requires Windows 10. Its API sets/UCRT are OS components, not
    # build-host files to copy into a portable application's search path.
    if _leaf.startswith('api-ms-win-') or _leaf == 'ucrtbase.dll':
        continue
    if _leaf in _runtime_dll_names:
        _pinned = _runtime_dll_root / _leaf
        if not _pinned.is_file():
            raise FileNotFoundError(f'Pinned runtime dependency missing: {_pinned}')
        _source = str(_pinned)
    _runtime_binaries.append((_destination, _source, _typecode))
a.binaries = type(a.binaries)(_runtime_binaries)
# Qt6Core uses Windows' ICU API. Do not redistribute System32 DLLs or collect
# a host Poppler ICU with incompatible version-suffixed exports. Windows
# supplies its matching system ICU through the normal DLL search path.
_qt_dependency_copies = []
_qt_dependency_basenames = {
    "shiboken6.abi3.dll",
}
a.binaries = type(a.binaries)(
    item
    for item in a.binaries
    if Path(item[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
)
for _name, _source, _typecode in a.binaries:
    _basename = Path(_name).name.lower()
    if _basename in _qt_dependency_basenames:
        _qt_dependency_copies.append(
            (f"PySide6/{Path(_name).name}", _source, _typecode)
        )
if _qt_dependency_copies:
    a.binaries = type(a.binaries)([*a.binaries, *_qt_dependency_copies])
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI-Piano-Studio",
    icon=str(package_root / "resources" / "brand" / "flowing-piano.ico"),
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AI-Piano-Studio",
)
