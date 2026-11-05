"""Keep unneeded browser binaries out without removing the music UI runtime."""
import ast
from pathlib import Path

import pytest


@pytest.fixture
def excludes_browser_component():
    spec = Path(__file__).parents[2] / 'ai_piano_studio.spec'
    tree = ast.parse(spec.read_text(encoding='utf-8'))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == '_unused_browser_component')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(spec), 'exec'), namespace)
    return namespace[function.name]


@pytest.mark.parametrize('path', [
    'PySide6/Qt6WebEngineCore.dll', 'PySide6/Qt6PdfQuick.dll',
    'PySide6/Qt6WebView.dll', 'PySide6/qml/QtWebEngine/qmldir',
    'PySide6/qml/QtWebView/qtwebviewquickplugin.dll',
    'PySide6/qml/QtQuick/Pdf/pdfplugin.dll',
    'PySide6/plugins/webview/qtwebview_webengine.dll',
    'PySide6/resources/qtwebengine_resources.pak',
    'PySide6/resources/icudtl.dat',
    'PySide6/plugins/imageformats/qpdf.dll',
    'PySide6/QtWebEngineCore.pyd',
])
def test_browser_and_pdf_components_are_excluded(excludes_browser_component, path):
    assert excludes_browser_component(path)
    assert excludes_browser_component(path.replace('/', '\\'))


@pytest.mark.parametrize('path', [
    'PySide6/Qt6Multimedia.dll', 'PySide6/Qt6Quick.dll',
    'PySide6/Qt6Qml.dll', 'PySide6/icuuc.dll',
    'PySide6/qml/QtQuick/Controls/qmldir',
    'PySide6/qml/QtMultimedia/multimediaquickplugin.dll',
    'ai_piano_studio/resources/third-party/upstream/qt/qtwebview/LICENSE',
])
def test_music_ui_and_license_materials_are_retained(excludes_browser_component, path):
    assert not excludes_browser_component(path)
