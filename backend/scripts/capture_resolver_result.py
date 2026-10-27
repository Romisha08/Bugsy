from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
_DESIGNATED_RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
FIXTURE = "requests>=2.32,<3\n"


def _validated_output_path() -> Path:
    designated = _DESIGNATED_RESULTS_DIR
    if RESULTS_DIR != designated or RESULTS_DIR.is_symlink():
        raise ValueError("Capture path is outside the designated local results directory.")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = RESULTS_DIR / "resolver-result.json"
    if output.is_symlink():
        raise ValueError("Capture path cannot be a symbolic link.")
    if output.exists():
        raise FileExistsError("Capture already exists; refusing to overwrite or run again.")
    if output.resolve().parent != RESULTS_DIR.resolve():
        raise ValueError("Capture path is outside the designated local results directory.")
    return output


def run_once(
    *,
    adapter_factory: Callable[[], object] | None = None,
    printer: Callable[[str], None] = print,
) -> tuple[object | None, bool]:
    if os.environ.get("SANDBOX_ENABLED", "").strip().lower() != "true":
        printer("Sandbox operation not started: process SANDBOX_ENABLED must be true.")
        return None, False

    try:
        output_path = _validated_output_path()
    except (OSError, ValueError, FileExistsError) as exc:
        printer(f"CAPTURE_PATH_ERROR={type(exc).__name__}; operation not started.")
        return None, False

    if adapter_factory is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.adapters.contree import ContreeResolverAdapter
        from app.adapters.resolver_capture import capture_before_print

        adapter_factory = ContreeResolverAdapter
    else:
        from app.adapters.resolver_capture import capture_before_print

    # Exactly one adapter construction and resolve call; there is no retry path.
    adapter = adapter_factory()
    result = adapter.resolve(FIXTURE, "3.12")
    saved = capture_before_print(result, output_path, printer)
    return result, saved


def main() -> int:
    result, saved = run_once()
    if result is None:
        return 2
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
