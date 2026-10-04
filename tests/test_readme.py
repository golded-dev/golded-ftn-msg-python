"""Run the public README example against the active installation."""

from pathlib import Path

import pytest


def test_readme_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    example = readme.read_text().split("```python\n", 1)[1].split("```", 1)[0]
    monkeypatch.chdir(tmp_path)
    exec(compile(example, str(readme), "exec"), {})
    assert (tmp_path / "messages" / "1.MSG").is_file()
