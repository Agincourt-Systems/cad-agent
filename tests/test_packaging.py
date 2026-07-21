"""Guards for dependency-declaration invariants (ADR 0030).

These tests protect two defects from the deficiency log
(``docs/specs/arm-deficiencies.md``) against silent regression:

- **D-001** — build123d 0.11.x breaks cadx (the ``interference`` check
  silently passes on overlapping parts; the renderer's assembly-combine path
  crashes). The declared dependency must therefore *exclude* the 0.11 line
  while still admitting supported 0.10.x patches.
- **D-002** — the DXF parse-back tests ``importorskip("ezdxf")``, so a
  README-faithful install that omits ``ezdxf`` silently skips load-bearing
  DXF verification. ``ezdxf`` must be declared in the ``test`` extra.

The assertions parse ``pyproject.toml`` and evaluate the *version specifier*
semantically (via ``packaging``) rather than string-matching, so they hold
regardless of exactly how the cap is spelled.
"""

from __future__ import annotations

from pathlib import Path

# tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
try:  # pragma: no cover - trivial import shim
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from packaging.requirements import Requirement
from packaging.version import Version


def _load_pyproject() -> dict:
    """Parse the repository's pyproject.toml as a dict."""

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        return tomllib.load(handle)


def _requirement(extras: dict[str, list[str]], extra: str, dist_name: str) -> Requirement:
    """Return the parsed Requirement for ``dist_name`` in the named extra.

    Fails the test (rather than raising a bare KeyError) if the dependency is
    absent, so a dropped declaration reports as a clear assertion failure.
    """

    specs = extras.get(extra, [])
    for raw in specs:
        req = Requirement(raw)
        if req.name.lower() == dist_name.lower():
            return req
    raise AssertionError(
        f"{dist_name!r} is not declared in the [{extra}] optional-dependencies; "
        f"found {specs!r}"
    )


def test_build123d_requirement_excludes_0_11() -> None:
    """D-001: the build123d range admits 0.10.x but rejects 0.11+."""

    project = _load_pyproject()
    extras = project["project"]["optional-dependencies"]
    req = _requirement(extras, "cad", "build123d")
    spec = req.specifier

    # A representative supported 0.10.x patch must remain installable.
    assert spec.contains(Version("0.10.0")), (
        f"build123d 0.10.0 must satisfy the declared range, got {str(spec)!r}"
    )
    # The 0.11 line (and anything past it) breaks the interference check and
    # the renderer, so it must be excluded by an upper bound.
    for blocked in ("0.11.0", "0.11.1", "0.12.0"):
        assert not spec.contains(Version(blocked)), (
            f"build123d {blocked} must be excluded by the cap, "
            f"but it satisfies {str(spec)!r} (D-001 regression)"
        )


def test_ezdxf_declared_in_test_extra() -> None:
    """D-002: ezdxf is declared so DXF parse-back tests actually run."""

    project = _load_pyproject()
    extras = project["project"]["optional-dependencies"]
    # Raises AssertionError with a helpful message if absent.
    _requirement(extras, "test", "ezdxf")
