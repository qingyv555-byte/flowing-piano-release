# Native runtime components

The application's GPL license does not replace the licenses of its runtime libraries.

- Python 3.12.16 comes from python-build-standalone release 20261026. The pinned
  install-only-stripped archive matches the installed python312.dll; the companion
  full archive retains build metadata and dependency notices. The pinned build
  scripts describe the sources and compiler options used by that distribution.
- OpenSSL 3.5.9, SQLite and libffi DLLs come from that Python runtime. Packaging
  explicitly selects those files rather than a different installation on PATH.
  Their original notice paths are indexed in `UPSTREAM-NOTICES.json`.
- PySide6/Shiboken and Qt 6.12.0 are dynamically linked and retain their upstream
  license texts. The dependency source bundle contains the matching Qt and PySide
  source archives. This application uses no Qt WebEngine, WebView or PDF modules;
  their binaries and plugins are excluded from the distributable.
- FFmpeg 9.0.1 is provided by the Qt wheel and retains its LGPL licensing. Its
  source archive and license texts accompany this candidate.
- The optional Windows OpenGL software fallback in the Qt wheel is Mesa 11.2.2
  with LLVM 3.6.2, identified by its embedded version strings. The matching
  upstream source archives and notices are retained. Qt's build information:
  https://wiki.qt.io/MesaLlvmpipe . It is separate from the Qt source archive.
- NumPy's wheel includes its OpenBLAS and compiler-runtime attributions. All its
  license content is retained, with subcomponent paths indexed in `DEPENDENCIES.json`.
- Microsoft Visual C++ 2015–2022 Runtime files retain Microsoft's terms in
  `LICENSE-MSVC-RUNTIME.docx` (the original Microsoft document), with a text copy
  in `LICENSE-MSVC-RUNTIME.txt`. The extraction source and SHA-256 are recorded in
  `MICROSOFT-RUNTIME-SOURCE.json`. Redistribution information:
  https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files .
- Windows API-set, UCRT and system ICU files are supplied by the supported
  Windows 10/11 installation; this package does not copy them from System32.

`DEPENDENCIES.json` records the build environment, including development tools
which may not be present in the runtime. `SOURCE-ARCHIVES.json` records the exact
archive hashes. The source snapshots and candidate hashes accompany the package;
these materials do not substitute for the remaining device and release acceptance.

Upstream notices keep their original bytes in short unique filenames to avoid
Windows installer path-length limits. `UPSTREAM-NOTICES.json` maps every file to
its original archive/path and content hash; `NOTICE-FILES.json` checks the complete
shipped notice tree. Original full source paths remain in the source archives.
