# Project Quality Constraints

Last reviewed: 2026-09-02

This file records the project's targeted-testing policy. It is the canonical
quality contract for future implementation tasks. Do not weaken it to make a
change pass.

## Floor (always enforced)

- Do not add secrets, credentials, or private API keys to source, tests, or
  committed artifacts.
- Do not add suppression comments or flags that hide a failing check without
  documenting an approved exception.
- Do not skip, delete, or weaken an existing test without recording the reason
  in the same change.
- Do not leave unimplemented stubs or empty exception handlers in production
  code.
- Preserve unrelated user changes and untracked user-owned files.

## Layered verification

| Level | When it runs | Required checks |
|---|---|---|
| 1 | During ordinary implementation | New tests, directly related unit tests, and the smallest relevant smoke check only. |
| 2 | At ordinary task completion | Affected tests/related module integration tests, plus `py -3.12 -m compileall -q src tests tools` and `git diff --check` when applicable. |
| 3 | When shared core behavior changes | Expand regression only to the affected dependency surface. This includes `ProjectState`, `History`, save/load schema, Audio Engine, AgentLoop, and Tool Gateway changes. |
| 4 | Release Candidate, main-branch merge, or package build | Full `pytest`, Portable build/smoke, Setup build/install/launch/uninstall smoke, and all release checks. |

## Scope and repetition rules

- Ordinary UI, panel, button, and local save-experience changes must not run
  the 1700+ full suite by default.
- A passed test stays valid when the code it covers has not changed; do not
  repeat it without a changed dependency or a new failure signal.
- If a change spans multiple levels, use the highest applicable level.
- Record the selected level, commands, and results in the task's persistent
  progress record.

## Assistant workflow regression floor

For changes to assistant context construction, request routing, source adoption,
execution modes, proposals or Music Tools, include these focused integration
tests in the affected checks:

- `tests/music_agent/test_imported_song_extension.py`
- `tests/music_agent/test_assistant_supplements.py`

Keep coverage for real multi-track MIDI import, audition/adoption, subsequent
editing and duration extension in the same controller, bounded authorization,
proposal versus explicit direct application, save/load and one-step undo.
Single-layer fixtures or presentation-only screenshots do not replace this
workflow check. This floor adds targeted checks; it does not require the full
suite for ordinary assistant work.

## Release gate

Level 4 is mandatory before claiming a release package is ready or merging to
`main`. A failed gate must be fixed or explicitly reported; it must not be
silenced by lowering the scope or removing assertions.
