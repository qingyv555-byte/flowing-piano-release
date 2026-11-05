"""Standalone optional-runtime entry point; never imports the GUI application."""
from pathlib import Path
import sys
from zipfile import ZipFile


def convert_score(source: Path, target: Path):
    # Validate XML/archive bounds before handing data to the mature score parser.
    if source.suffix.casefold() == ".mxl":
        with ZipFile(source) as archive:
            if len(archive.infolist()) > 128 or sum(i.file_size for i in archive.infolist()) > 8_000_000:
                raise ValueError("oversized score archive")
            for item in archive.infolist():
                if not item.is_dir() and b"<!ENTITY" in archive.read(item).upper():
                    raise ValueError("score entity declarations are not supported")
    elif b"<!ENTITY" in source.read_bytes().upper():
        raise ValueError("score entity declarations are not supported")
    from music21 import converter
    score = converter.parse(str(source), format="musicxml", forceSource=True)
    if len(score.recurse().notes) > 30000:
        raise ValueError("too many score notes")
    score.write("midi", fp=str(target))


def transcribe(source: Path, target: Path):
    import soundfile as sf
    info = sf.info(source)
    if not 0 < info.duration <= 180 or info.channels > 2:
        raise ValueError("audio exceeds the short transcription limit")
    import basic_pitch
    import onnxruntime as ort
    from basic_pitch.inference import Model, predict
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    model_path = basic_pitch.build_icassp_2022_model_path(basic_pitch.FilenameSuffix.onnx)
    model = Model.__new__(Model)
    model.model_type = Model.MODEL_TYPES.ONNX
    model.model = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    _outputs, midi, _events = predict(str(source), model_or_model_path=model,
        minimum_frequency=27.5, maximum_frequency=4186.01)
    midi.write(str(target))


if __name__ == "__main__":
    kind, raw_source, raw_job = sys.argv[1:]
    source, job = Path(raw_source), Path(raw_job)
    if not source.is_file() or not job.is_dir():
        raise ValueError("music runtime requires a local file and an owned job directory")
    if kind == "score":
        convert_score(source, job / "result.mid")
    elif kind == "audio":
        transcribe(source, job / "result.mid")
    else:
        raise ValueError("unsupported music runtime mode")
