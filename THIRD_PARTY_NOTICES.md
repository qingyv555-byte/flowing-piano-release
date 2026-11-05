# Third-Party Notices

## Mutopia Moonlight Sonata, first movement MIDI

Bundled resource: `src/ai_piano_studio/resources/music_catalog/moonlight1.mid`.
Composition by Ludwig van Beethoven; typeset by Chris Sawer and Stewart Holmes.
The Mutopia Project, Mutopia-2007/02/11-276. Source edition: Berners 1908,
edited by A. Winterberger. This specific typeset edition is CC BY-SA 2.5.
Source: https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=276
License: https://creativecommons.org/licenses/by-sa/2.5/
The bundled MIDI bytes are unchanged. App-created arrangements can change
velocity/tempo; their project folder carries attribution and a changes notice.
The score edition is not a particular commercial sound recording.

## Optional music conversion runtime

Spotify Basic Pitch 0.4.0, copyright 2022 Spotify AB, Apache-2.0.
https://github.com/spotify/basic-pitch
Uses the upstream distributed ICASSP 2022 ONNX model and inference API.
No upstream inference code has been copied into the main application.
Its installed LICENSE and NOTICE remain with the optional runtime.
ONNX Runtime 1.31.1: Microsoft, MIT License.
https://github.com/microsoft/onnxruntime
music21 10.5.2: Michael Scott Asato Cuthbert and contributors, BSD-3-Clause.
https://github.com/cuthbertLab/music21
These dependencies and their own dependency notices are installed separately
by `tools/Install-MusicRuntime.ps1`; the main app does not require or bundle
TensorFlow, PyTorch, Demucs, MT3, Omnizart or this optional runtime.

## Salamander Grand Piano v3

Author: Alexander Holm
License: Creative Commons Attribution 3.0 Unported (CC BY 3.0)
License terms: https://creativecommons.org/licenses/by/3.0/
Source: official Salamander Grand Piano v3 project

Modifications:

1. Selected root-note samples.
2. Selected velocity layers.
3. Resampled.
4. Cropped.
5. Integrated into AI Piano Studio.

Alexander Holm does not endorse or participate in AI Piano Studio.
AI Piano Studio is not affiliated with Alexander Holm.

## Source and attribution records

Salamander download and checksums: `resources/piano/PROVENANCE-SALAMANDER.md`.
The source archive URL is recorded there. Its CC BY 3.0 terms remain in force;
the project GPL license does not replace them.

## Lucide icons

Copyright (c) 2020 Lucide Contributors. License: MIT.
Source: https://lucide.dev/ and https://github.com/lucide-icons/lucide
Full retained notice: `src/ai_piano_studio/qml/icons/LICENSE.lucide`.
The packaged QML icon directory retains this notice.

## Lobe Icons — AI brand assets

Copyright (c) 2023 LobeHub. License: MIT.
Source: https://github.com/lobehub/lobe-icons
Revision: `8b7bbc433d38437fa2b0cca58ae896917162b812`.
Selected static SVGs are packaged as `qml/icons/brand-*.svg` (OpenAI,
DeepSeek, Claude, Anthropic, Gemini, Qwen, Bailian, Kimi, Moonshot, Ollama,
Z.AI / GLM, Doubao, MiniMax, Volcengine and OpenRouter).
Selected monochrome SVG geometry is also embedded in `qml/AIBrandArtwork.js`
and rendered with the application's theme color at the user's request.
Full retained notice: `src/ai_piano_studio/qml/icons/LICENSE.lobe-icons`.
`brand-generic.svg` is original project artwork. Brand marks identify services;
their owners do not endorse this application.

## Dependencies and release scope

The optional music runtime additionally uses pypdfium2 5.14.0
(https://github.com/pypdfium2-team/pypdfium2), licensed under Apache-2.0 or
BSD-3-Clause. Its bundled PDFium binaries retain their own BSD-style and
third-party notices; keep the installed package's license files with any
redistributed runtime. This component renders source score pages in a short
isolated process; it does not establish rights to redistribute a third-party
score or verify the accuracy of an AI transcription.

Python, PySide6/Qt, NumPy, keyring and their transitive dependencies retain
their respective notices. This document is not a complete license inventory
of a built installer. Each release must collect notices for the exact shipped
versions and fulfill applicable source and redistribution obligations; see
`docs/RELEASING.md`. No dependency is relicensed by the project GPL notice.

## Example music

Score references and arrangement boundaries: `docs/TEMPLATE-SOURCES.md`.
Modern score editions are not all public domain; their individual notices
must not be replaced with a blanket GPL or public-domain claim.

## Optional private character artwork

The five promotional character images previously used for local preview have no
public redistribution authorization and are excluded from the current source
and binary distributions. They are not covered by the project's GPL notice.
Existing local copies are preserved in the user's profile under
`backgrounds/character-skins/`; a clean installation lists no missing skin cards.
Users can still import their own background artwork. Local possession or free
software distribution does not establish permission to redistribute artwork.
