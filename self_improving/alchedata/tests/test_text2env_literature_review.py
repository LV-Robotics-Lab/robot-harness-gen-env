from scripts.text2env_literature_review import (
    REQUIRED_CAPABILITIES,
    REQUIRED_CATEGORIES,
    read_json,
    validate_review_package,
    METHOD_MATRIX,
    SOURCE_REGISTRY,
)


def test_text2env_literature_review_package_passes() -> None:
    report = validate_review_package()

    assert report["status"] == "pass_text2env_literature_review_package"
    assert report["source_count"] == 20
    assert report["academic_primary_source_count"] == 17
    assert report["matrix_rows"] == 20
    assert report["matrix_capabilities"] == 8
    assert report["acceptance_items"] == 7
    assert report["source_page_screenshots"] == 19


def test_source_links_and_functional_taxonomy_are_explicit() -> None:
    registry = read_json(SOURCE_REGISTRY)
    categories = {category for source in registry["sources"] for category in source["categories"]}

    assert categories == REQUIRED_CATEGORIES
    for source in registry["sources"]:
        if source["open_status"]["code_status"] == "released":
            assert source["links"]["code"].startswith("https://")


def test_method_matrix_has_every_required_capability_for_every_source() -> None:
    matrix = read_json(METHOD_MATRIX)

    assert set(matrix["capabilities"]) == REQUIRED_CAPABILITIES
    assert all(set(row["scores"]) == REQUIRED_CAPABILITIES for row in matrix["rows"])
