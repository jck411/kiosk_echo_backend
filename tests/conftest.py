import pathlib
import sys

import psutil
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="session", autouse=True)
def cleanup_processes():
    """Kill any lingering child processes after all tests complete."""
    yield

    try:
        current_process = psutil.Process()
        children = current_process.children(recursive=True)

        if children:
            print(f"\n[CLEANUP] Found {len(children)} child processes, terminating...")

        for child in children:
            try:
                print(f"[CLEANUP] Terminating process {child.pid} ({child.name()})")
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        _, alive = psutil.wait_procs(children, timeout=0.5)
        for child in alive:
            try:
                print(f"[CLEANUP] Force killing process {child.pid}")
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(alive, timeout=0.5)

        print("[CLEANUP] Process cleanup complete")
    except Exception as e:
        print(f"[CLEANUP] Error during cleanup: {e}")
