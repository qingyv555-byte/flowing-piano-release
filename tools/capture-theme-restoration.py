"""Isolated real-QML evidence; never touches the user's project or audio device."""
from pathlib import Path
import json
import os
import tempfile

os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QSG_RENDER_LOOP="basic")
from studio_evidence import install_memory_output

blocked = install_memory_output()
from PySide6.QtCore import QObject, QCoreApplication, QEvent
from PySide6.QtQml import QQmlExpression
from PySide6.QtTest import QTest
from ai_piano_studio.studio import build_studio_engine
from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.appearance.character_skins import SKINS
from ai_piano_studio.music_agent import assistant_controller

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/theme-restore-20261101/evidence"
OUT.mkdir(parents=True, exist_ok=True)
assistant_controller.detect_codex = lambda **kwargs: dict(installed=False, version="", login="", supported=False, message="Offline visual check")


def main():
    with tempfile.TemporaryDirectory(prefix="flowing-theme-review-") as directory:
        app, engine, editor, playback, keyboard = build_studio_engine(Path(directory))
        root = engine.rootObjects()[0]
        root.resize(1440, 900)
        initialize_deferred_services(engine)
        root.setProperty("pageState", "editor")
        state = ProjectState(notes=tuple(Note.create(id=f"theme-note-{i}", pitch=60+i%12,
            start_time=i*.35, duration=.75) for i in range(20)))
        editor._replace_project(state, saved_content=state.content, path=None)
        appearance = engine.rootContext().contextProperty("studioAppearance")
        rows=[]
        for key in SKINS:
            appearance.applyCharacterTheme(key)
            QTest.qWait(100)
            def value(expression):
                result=QQmlExpression(engine.rootContext(),root,expression).evaluate()
                return result[0] if isinstance(result,tuple) else result
            assert appearance.characterSkins==[]
            assert value("appTheme.paper") is False
            color=value("appTheme.appBackground").name()
            assert root.grabWindow().save(str(OUT/f"editor-{key}.png"))
            rows.append(dict(theme=key,seed=appearance.themeSeed.name(),app_background=color,
                             native_note_material=appearance.values["characterSkin"],artwork_choices=0))
        assert len({r["app_background"] for r in rows})==5
        root.findChild(QObject,"studioSettingsShortcut").activated.emit()
        QTest.qWait(150)
        dialog=root.findChild(QObject,"studioAppearanceDialog")
        assert dialog is not None and dialog.property("opened")
        assert root.grabWindow().save(str(OUT/"theme-settings.png"))
        assert editor.state.content==state.content and not blocked
        (OUT.parent/"visual-verification.json").write_text(json.dumps(dict(
            themes=rows,hardware_calls=blocked,user_profile_touched=False,
            scene="Real Qt editor/settings without private images",visual_inspection="pending"),indent=2),encoding="utf-8")
        dialog.close()
        playback.stop()
        root.close()
        engine.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        print(json.dumps(dict(themes=rows,hardware_calls=blocked)),flush=True)


if __name__=="__main__":main()
