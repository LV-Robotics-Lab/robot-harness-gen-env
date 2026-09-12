import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import writer_paths  # noqa: E402


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        ".",
        "..",
        "../victim",
        "bad/name",
        "bad\\name",
        "bad\x00name",
        "bad\nname",
        "a:b",
        "CON",
        "name.",
        "space name",
        "资产",
    ],
)
def test_portable_segment_rejects_overwrite_and_delete_path_attacks(value):
    with pytest.raises(writer_paths.UnsafeWriterPathError):
        writer_paths.portable_segment(value, field="asset")


def test_portable_segment_accepts_one_portable_component():
    assert writer_paths.portable_segment("301_blue-cup.v2", field="asset") == "301_blue-cup.v2"


def test_contained_path_rejects_escape_and_symlink_components(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(writer_paths.UnsafeWriterPathError, match="escapes"):
        writer_paths.contained_path(library, library / "nvidia" / ".." / ".." / "victim")

    (library / "nvidia").symlink_to(outside, target_is_directory=True)
    with pytest.raises(writer_paths.UnsafeWriterPathError, match="symlink"):
        writer_paths.contained_path(library, library / "nvidia" / "301_cup")


def test_contained_path_accepts_nonexistent_child_under_regular_root(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    expected = library / "github" / "301_cup"

    assert writer_paths.contained_path(library, expected) == expected.absolute()
