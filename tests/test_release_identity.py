from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from releaseledger.domain.release import release_identity_key
from releaseledger.errors import LaunchError
from releaseledger.services.releases import (
    create_release,
    resolve_release_selector,
    show_release,
)
from releaseledger.storage.paths import ensure_canonical_project


def test_release_identity_key_normalizes_only_semver_v_prefix() -> None:
    assert release_identity_key("v0.1.0") == "0.1.0"
    assert release_identity_key("0.1.0") == "0.1.0"
    assert release_identity_key("v1.2.3-rc.1") == "1.2.3-rc.1"
    assert release_identity_key("1.2.3-rc.1") == "1.2.3-rc.1"
    assert release_identity_key("v1.2.3+linux") == "1.2.3+linux"
    assert release_identity_key("release-2026-08-21") == "release-2026-08-21"
    assert release_identity_key("vrelease-2026-08-21") == "vrelease-2026-08-21"
    assert release_identity_key("vv1.2.3") == "vv1.2.3"


def test_unique_alias_selector_resolves_and_show_uses_raw_bundle(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0")

    assert resolve_release_selector(tmp_path, "v0.1.0") == "0.1.0"
    assert show_release(tmp_path, "v0.1.0")["release"]["version"] == "0.1.0"


def test_ambiguous_alias_selector_fails_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_canonical_project(tmp_path)
    monkeypatch.setattr(
        "releaseledger.services.releases.list_releases",
        lambda _root: [
            SimpleNamespace(version="v1.2.3"),
            SimpleNamespace(version="v1.2.3"),
        ],
    )
    with pytest.raises(LaunchError) as exc_info:
        resolve_release_selector(tmp_path, "1.2.3")
    assert exc_info.value.code == "CONFLICT"
    assert exc_info.value.data["matches"] == ["v1.2.3", "v1.2.3"]
