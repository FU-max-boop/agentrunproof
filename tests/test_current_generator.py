from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_current_certificate.py"


def test_current_worker_cannot_overlay_checkout_source() -> None:
    module = _script_module()
    worker = module._NETWORK_GUARDED_WORKER

    assert "sys.path.insert" not in worker
    assert "sys.argv" not in worker
    assert '"site-packages" not in module.parts' in worker


def test_installed_wheel_probe_binds_module_and_archive_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _script_module()
    environment = tmp_path / "environment"
    python = environment / "bin" / "python"
    installed_module = (
        environment / "lib" / "python3.12" / "site-packages" / "agentrunproof" / "__init__.py"
    )
    wheel = tmp_path / "agentrunproof-0.1.1-py3-none-any.whl"
    wheel.write_bytes(b"bound wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()

    def probe(_command: list[str]) -> str:
        return json.dumps(
            {
                "module": str(installed_module),
                "direct_url": {
                    "archive_info": {"hashes": {"sha256": digest}},
                    "url": wheel.as_uri(),
                },
            }
        )

    monkeypatch.setattr(module, "_capture", probe)
    module._assert_installed_wheel(python, wheel=wheel)

    outside = ROOT / "src" / "agentrunproof" / "__init__.py"

    def checkout_probe(_command: list[str]) -> str:
        payload = json.loads(probe(_command))
        payload["module"] = str(outside)
        return json.dumps(payload)

    monkeypatch.setattr(module, "_capture", checkout_probe)
    with pytest.raises(RuntimeError, match="fresh environment"):
        module._assert_installed_wheel(python, wheel=wheel)

    def wrong_hash_probe(_command: list[str]) -> str:
        payload = json.loads(probe(_command))
        payload["direct_url"]["archive_info"]["hashes"]["sha256"] = "0" * 64
        return json.dumps(payload)

    monkeypatch.setattr(module, "_capture", wrong_hash_probe)
    with pytest.raises(RuntimeError, match="bundle wheel"):
        module._assert_installed_wheel(python, wheel=wheel)


def _script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_current_certificate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
