import pytest
from dataclasses import replace

from app.services.requirements_parser import RequirementsInputError, parse_requirements


def test_valid_constraints_extras_and_environment_markers_are_preserved() -> None:
    text = "# project dependencies\nNumPy[fast]>=1.26,<2 # upper bound for this project\nimportlib-metadata; python_version < '3.10'\n"

    result = parse_requirements(text)

    assert result.issues == ()
    assert result.requirements[0] == {
        "line": 2,
        "original": "NumPy[fast]>=1.26,<2 # upper bound for this project",
        "name": "numpy",
        "specifier": "<2,>=1.26",
        "extras": ["fast"],
        "marker": None,
    }
    assert result.requirements[1]["line"] == 3
    assert result.requirements[1]["marker"] == "python_version < \"3.10\""
    assert result.source_sha256


def test_malformed_and_unsupported_lines_block_full_parse_with_original_lines() -> None:
    result = parse_requirements("requests>=2\n-e ./local-package\nbroken =>=3\n-c constraints.txt\n")

    assert [issue.line for issue in result.issues] == [2, 3, 4]
    assert [issue.original for issue in result.issues] == ["-e ./local-package", "broken =>=3", "-c constraints.txt"]
    assert len(result.requirements) == 1


def test_direct_url_is_unsupported_even_when_pep508_can_parse_it() -> None:
    result = parse_requirements("private-lib @ https://packages.example/private.whl\n")

    assert result.requirements == ()
    assert result.issues[0].line == 1
    assert "Direct URL" in result.issues[0].reason


def test_bom_is_accepted_without_changing_source_hash() -> None:
    text = "\ufeffrequests==2.32.4\n"

    result = parse_requirements(text)

    assert result.issues == ()
    assert result.requirements[0]["name"] == "requests"


def test_hash_character_inside_a_valid_marker_is_not_treated_as_comment() -> None:
    result = parse_requirements('demo; sys_platform == "linux # test"\n')
    assert result.issues == ()
    assert "# test" in result.requirements[0]["marker"]


def test_blank_or_comment_only_input_is_rejected() -> None:
    with pytest.raises(RequirementsInputError, match="at least one"):
        parse_requirements("# no packages yet\n\n")


def test_input_line_and_size_limits_are_enforced(monkeypatch) -> None:
    from app.services import requirements_parser

    monkeypatch.setattr(requirements_parser, "settings", replace(requirements_parser.settings, max_requirements_bytes=10))
    with pytest.raises(RequirementsInputError, match="exceeds"):
        parse_requirements("requests>=2\n")

    monkeypatch.setattr(requirements_parser, "settings", replace(requirements_parser.settings, max_requirements_bytes=65536, max_requirements_lines=1))
    with pytest.raises(RequirementsInputError, match="line limit"):
        parse_requirements("requests>=2\n# more lines\n")


def test_non_python_three_target_is_rejected():
    from app.services.requirements_parser import validate_python_version

    with pytest.raises(RequirementsInputError, match="Choose Python"):
        validate_python_version("2.7")


@pytest.mark.parametrize("target", ["3.10", "3.11", "3.12", "3.13", "3.14"])
def test_supported_python_targets_are_accepted(target):
    from app.services.requirements_parser import RequirementsInputError, validate_python_version

    assert validate_python_version(target) == target


@pytest.mark.parametrize("target", ["3.9", "3.15", "3.12.14", "4.0"])
def test_unsupported_python_targets_are_rejected(target):
    from app.services.requirements_parser import RequirementsInputError, validate_python_version

    with pytest.raises(RequirementsInputError):
        validate_python_version(target)
