from pathlib import Path


EXPECTED_CASE_IDS = {
    "continue",
    "sadder-selection",
    "add-chords",
    "make-climax",
    "simplify",
    "richer-rhythm",
    "explicit-range-priority",
    "malformed-output",
    "cancel-discard",
    "project-switch",
}


def test_wave2_evaluation_matrix_contains_all_required_cases() -> None:
    matrix = (
        Path(__file__).parents[2]
        / "docs"
        / "product-polish"
        / "wave2-evaluation.md"
    ).read_text(encoding="utf-8")
    actual_ids = {
        line.split("`")[1]
        for line in matrix.splitlines()
        if line.startswith("| `") and line.count("`") >= 2
    }
    assert actual_ids == EXPECTED_CASE_IDS
