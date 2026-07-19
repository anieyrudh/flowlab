import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POINTER = Path(
    "benchmarks/cases/open-boundary/campaigns/validated-campaign-pointer.json"
)


def test_desktop_bundle_copies_the_digest_checked_campaign_gate() -> None:
    pointer = json.loads((ROOT / POINTER).read_text(encoding="utf-8"))
    report = Path(pointer["report"])
    report_path = (ROOT / report).resolve()

    assert report_path.is_relative_to(ROOT.resolve())
    assert report_path.is_file()
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == pointer["sha256"]

    build_script = (ROOT / "scripts/build_macos_app.sh").read_text(encoding="utf-8")
    assert f'copy_evidence "{POINTER}"' in build_script
    assert f'copy_evidence "{report}"' in build_script
