# Wave 2 — Deterministic AI Music Evaluation Matrix

This matrix is the offline acceptance fixture for the Wave 2 AI music-quality
work. It deliberately tests intent routing, context priority, safety guards,
proposal behavior, and recovery without making Provider calls or spending API
quota. A live Provider/device run is a separate manual gate and is not implied
by the automated status below.

## Fixture assumptions

- Project: 120 BPM, 4/4, C minor, 16 bars, piano instrument.
- Selection fixture: notes in bars 5–8, pitches C4–G5, with a short motif and
  an existing bass line in bars 1–8.
- Manual creation scope fixture: bars 5–8, with note-write and project-read
  permissions; automatic creation scope is used only where the case says so.
- Every write case must return a concise proposal with range, operation counts,
  reason, and Audition / Apply / Discard actions.

## Cases

| ID | User request / setup | Expected deterministic behavior | Automated evidence |
| --- | --- | --- | --- |
| `continue` | Click “续写这段” on the selected motif. | Preserve motif context; route intent `continue`; write only the authorized/selected range unless the user explicitly names another range. | Intent-template and context-builder tests |
| `sadder-selection` | Select bars 5–8 and ask “更悲伤，但不要改主题”。 | Route the emotional intent; retain selected motif and prioritize selection over broad project metadata. | Context-priority tests + quick-action catalog |
| `add-chords` | Click “添加和弦” with a melody selection. | Read melody/harmony context, produce proposal operations, and avoid unrequested note-range expansion. | Tool/proposal contract tests |
| `make-climax` | Click “制造高潮” for bars 9–12 while another range is selected. | Explicit bars 9–12 override selection; intent guidance keeps the motif recognizable and avoids density explosion. | Explicit-range and intent-guidance tests |
| `simplify` | Ask to simplify the selected phrase. | Preserve contour and beat skeleton; delete/change only authorized notes and expose a discardable proposal. | Gateway quality/proposal tests |
| `richer-rhythm` | Ask for richer rhythm in bars 3–4. | Explicit range is parsed; no whole-project write permission is inferred from a local request. | Explicit-range controller tests |
| `explicit-range-priority` | Selection is bars 1–2; user says “只改第 9–12 小节”。 | Priority is explicit user text > selection; context has bars 9–12 and no selected-note IDs. | `test_explicit_bar_range_overrides_selection` |
| `malformed-output` | Provider returns malformed tool arguments or an overlong note. | Reject safely with a classified tool-validation/quality error; do not partially apply notes. | Tool-gateway validation tests |
| `cancel-discard` | Start a proposal, cancel or discard it before Apply. | Active turn exposes Cancel; discarded proposal produces no committed edit and remains retryable. | Assistant window/controller state tests |
| `project-switch` | Switch project between turns, then repeat the same action. | Rebuild context for the current project; stale proposal/context cannot silently apply to the new project. | Proposal staleness and context tests |

## Gate interpretation

- **VERIFIED** means the deterministic offline assertion is green.
- **MANUAL** means a human must still verify Provider availability, actual
  model output quality, audio audition, and token usage with the user’s own
  credentials.
- **BLOCKED** is reserved for a reproducible local failure that prevents the
  remaining Wave 2–5 work. A Provider being unavailable offline is not a local
  blocker.

Wave 2 automated coverage is required for all ten IDs before this Wave is
marked complete. The manual Provider matrix remains an explicit final-report
limitation until it is run with authorization and credentials.
