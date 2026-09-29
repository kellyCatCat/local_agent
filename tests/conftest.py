import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def multi_files() -> dict[str, str]:
    return {
        "SKILL.md": fixture("multi_main.md"),
        "reference/neighbor-down.md": fixture("ref_neighbor-down.md"),
        "reference/neighbor-flap.md": fixture("ref_neighbor-flap.md"),
    }
