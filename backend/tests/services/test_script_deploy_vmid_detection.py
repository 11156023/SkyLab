"""Regression tests for ``_detect_new_vmid`` in script_deploy_service.

Covers the concurrent-deployment race fix: the old before/after-diff +
``max()`` heuristic could claim (and on rollback, destroy) a container
created by ANOTHER deployment running at the same time.
"""

from __future__ import annotations

import pytest

from app.services.network import script_deploy_service as sds

_HOSTNAME = "my-ct"


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    all_vmids: set[int],
    resources: dict[int, dict] | None = None,
    by_hostname: int | None = None,
) -> None:
    monkeypatch.setattr(sds, "_get_all_vmids", lambda: set(all_vmids))
    monkeypatch.setattr(
        sds, "_find_resource_any", lambda vmid: (resources or {}).get(vmid)
    )
    monkeypatch.setattr(
        sds, "_find_vmid_by_hostname", lambda hostname: by_hostname  # noqa: ARG005
    )


def test_single_new_vmid_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, all_vmids={100, 101, 150})
    assert sds._detect_new_vmid({100, 101}, _HOSTNAME) == 150


def test_multiple_new_vmids_resolved_by_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two deployments finished concurrently — only the container whose name
    matches OUR hostname may be claimed, never ``max()``."""
    _patch(
        monkeypatch,
        all_vmids={100, 150, 160},
        resources={
            150: {"vmid": 150, "name": _HOSTNAME},
            160: {"vmid": 160, "name": "someone-elses-ct"},
        },
    )
    assert sds._detect_new_vmid({100}, _HOSTNAME) == 150


def test_multiple_new_vmids_without_match_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If none of the new containers matches our hostname we must NOT guess —
    guessing here is what allowed destroying someone else's container."""
    _patch(
        monkeypatch,
        all_vmids={100, 150, 160},
        resources={
            150: {"vmid": 150, "name": "other-a"},
            160: {"vmid": 160, "name": "other-b"},
        },
    )
    assert sds._detect_new_vmid({100}, _HOSTNAME) is None


def test_no_diff_falls_back_to_hostname_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cluster listing shows no diff (e.g. raced with the resource list), but a
    # later hostname lookup finds a vmid that did NOT exist before the deploy.
    _patch(monkeypatch, all_vmids={100}, by_hostname=150)
    assert sds._detect_new_vmid({100}, _HOSTNAME) == 150


def test_hostname_match_already_present_before_deploy_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing container with the same hostname is not ours."""
    _patch(monkeypatch, all_vmids={100, 150}, by_hostname=150)
    assert sds._detect_new_vmid({100, 150, 151}, _HOSTNAME) is None
