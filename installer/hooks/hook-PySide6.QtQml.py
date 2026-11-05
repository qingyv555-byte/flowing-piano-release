"""Collect QML's real import closure, using the application's fixed Basic style."""
import json
import logging
from pathlib import Path
import subprocess

from PyInstaller.utils.hooks import get_hook_config, get_package_paths
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


def hook(hook_api):
    root = Path(get_hook_config(hook_api, 'flowing_piano', 'qml_root')).resolve()
    _, package = get_package_paths('PySide6')
    scanner = Path(package) / 'qmlimportscanner.exe'
    qml = Path(pyside6_library_info.location['QmlImportsPath']).resolve()
    result = subprocess.run([str(scanner), '-rootPath', str(root), '-importPath', str(qml)],
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=60)
    scanned = json.loads(result.stdout)
    optional_styles = {'FluentWinUI3', 'Fusion', 'Imagine', 'Material', 'Universal', 'Windows', 'macOS', 'iOS'}
    allowed = set()
    module_names = []
    for module in scanned:
        if module.get('type') != 'module' or not module.get('path'):
            continue
        path = Path(module['path']).resolve()
        if not path.is_relative_to(qml):
            raise ValueError('QML scanner returned a module outside the Qt import tree')
        parts = path.relative_to(qml).parts
        if len(parts) >= 3 and parts[:2] == ('QtQuick', 'Controls') and parts[2] in optional_styles:
            continue
        allowed.add(path)
        module_names.append(module['name'])
    if not allowed or 'QtQuick.Controls.Basic' not in module_names:
        raise ValueError('The fixed Basic QML style was not resolved')

    def belongs_to_used_module(entry):
        source = Path(entry[0]).resolve()
        directory = source if source.is_dir() else source.parent
        while directory.is_relative_to(qml):
            if (directory / 'qmldir').is_file():
                return directory in allowed
            directory = directory.parent
        return False

    all_binaries, all_datas = pyside6_library_info.collect_qtqml_files()
    selected_binaries = [entry for entry in all_binaries if belongs_to_used_module(entry)]
    selected_datas = [entry for entry in all_datas if belongs_to_used_module(entry)
                      and Path(entry[0]).suffix.lower() not in {'.qmltypes', '.pdb', '.lib', '.exp', '.prl'}]
    hook_api.add_binaries(selected_binaries)
    hook_api.add_datas(selected_datas)
    report = get_hook_config(hook_api, 'flowing_piano', 'qml_report')
    Path(report).write_text(json.dumps({'modules': sorted(module_names), 'fixed_style': 'Basic',
                                      'qml_binaries': len(selected_binaries), 'qml_data_entries': len(selected_datas),
                                      'omitted_qml_binaries': len(all_binaries) - len(selected_binaries)}, indent=2)+'\n')
    logging.getLogger(__name__).info('Flowing Piano: collected %d QML modules; omitted %d unused QML binaries',
                                    len(allowed), len(all_binaries) - len(selected_binaries))
