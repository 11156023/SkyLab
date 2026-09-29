"""Tests for pure helpers in app.services.network.firewall_service.

Focus on the parsers/builders that are deterministic and free of any
Proxmox API or DB calls. Testing these guards against regressions in
the comment-format contract used by every SkyLab firewall rule.
"""

from __future__ import annotations

import pytest

from app.services.network import firewall_service as fw
from app.utils.hostname import from_punycode_hostname

# ─── _make_connection_comment / _parse_connection_comment round-trip ────────


def test_connection_comment_with_port_round_trip() -> None:
    comment = fw._make_connection_comment(101, 202, 8080, "tcp")
    parsed = fw._parse_connection_comment(comment)
    assert parsed == {
        "type": "connection",
        "source_vmid": 101,
        "target_vmid": 202,
        "port": 8080,
        "protocol": "tcp",
    }


def test_connection_comment_portless_round_trip() -> None:
    comment = fw._make_connection_comment(10, 20, 0, "icmp")
    parsed = fw._parse_connection_comment(comment)
    assert parsed == {
        "type": "connection",
        "source_vmid": 10,
        "target_vmid": 20,
        "port": 0,
        "protocol": "icmp",
    }


def test_parse_gateway_connection_with_port() -> None:
    parsed = fw._parse_connection_comment("SkyLab:101->gateway:443/tcp")
    assert parsed == {
        "type": "gateway_connection",
        "source_vmid": 101,
        "port": 443,
        "protocol": "tcp",
    }


def test_parse_gateway_connection_portless() -> None:
    parsed = fw._parse_connection_comment("SkyLab:101->gateway:icmp")
    assert parsed == {
        "type": "gateway_connection",
        "source_vmid": 101,
        "port": 0,
        "protocol": "icmp",
    }


def test_parse_internet_connection_with_port() -> None:
    parsed = fw._parse_connection_comment("SkyLab:gateway->101:80/tcp")
    assert parsed == {
        "type": "internet_connection",
        "target_vmid": 101,
        "port": 80,
        "protocol": "tcp",
    }


def test_parse_gateway_default_marker() -> None:
    assert fw._parse_connection_comment("SkyLab:gateway:default") == {
        "type": "gateway_default"
    }


def test_parse_unrelated_comment_returns_none() -> None:
    assert fw._parse_connection_comment("user-managed:foo") is None
    assert fw._parse_connection_comment("") is None


def test_parse_malformed_skylab_comment_returns_none() -> None:
    assert fw._parse_connection_comment("SkyLab:not-a-known-shape") is None


def test_parse_environment_network_comments_for_topology_only() -> None:
    icmp = fw._parse_topology_comment(
        "SkyLab:class-net:704664cd:101>102:icmp"
    )
    practice_tcp = fw._parse_topology_comment(
        "SkyLab:practice-net:704664cd:101>102:tcp/16000"
    )

    assert icmp == {
        "type": "connection",
        "source_vmid": 101,
        "target_vmid": 102,
        "protocol": "icmp",
        "port": 0,
        "topology_managed": True,
    }
    assert practice_tcp == {
        "type": "connection",
        "source_vmid": 101,
        "target_vmid": 102,
        "protocol": "tcp",
        "port": 16000,
        "topology_managed": True,
    }
    # The regular deletion parser must not recognize environment-managed rules.
    assert fw._parse_connection_comment(
        "SkyLab:class-net:704664cd:101>102:icmp"
    ) is None
    assert fw._parse_connection_comment(
        "SkyLab:practice-net:704664cd:101>102:icmp"
    ) is None


def test_edges_from_rules_includes_and_dedupes_environment_connection() -> None:
    comment = "SkyLab:practice-net:704664cd:101>102:icmp"

    edges = fw._edges_from_rules(
        {
            101: [{"pos": 0, "comment": comment}],
            102: [{"pos": 0, "comment": comment}],
        }
    )

    assert len(edges) == 1
    assert edges[0].source_vmid == 101
    assert edges[0].target_vmid == 102
    assert edges[0].ports == [fw.PortSpec(port=0, protocol="icmp")]
    assert edges[0].topology_managed is True


@pytest.mark.parametrize(
    "comment",
    [
        "SkyLab:class-net:704664cd:101>102:icmp",
        "SkyLab:practice-net:704664cd:101>102:icmp",
    ],
)
def test_regular_connection_delete_does_not_remove_environment_network_rules(
    monkeypatch, comment: str,
) -> None:
    deleted: list[int] = []
    monkeypatch.setattr(
        fw,
        "get_vm_firewall_rules",
        lambda *_args: [
            {
                "pos": 7,
                "comment": comment,
            }
        ],
    )
    monkeypatch.setattr(
        fw,
        "delete_rule_by_pos",
        lambda _node, _vmid, _resource_type, pos: deleted.append(pos),
    )

    fw._delete_matching_rules(
        node="pve1",
        vmid=101,
        resource_type="qemu",
        source_vmid=101,
        target_vmid=102,
        ports=None,
    )

    assert deleted == []


# ─── _make_rule_fields ───────────────────────────────────────────────────────


def test_make_rule_fields_with_port() -> None:
    assert fw._make_rule_fields(443, "tcp") == {"proto": "tcp", "dport": "443"}


def test_make_rule_fields_portless_omits_dport() -> None:
    fields = fw._make_rule_fields(0, "icmp")
    assert fields == {"proto": "icmp"}
    assert "dport" not in fields


# ─── from_punycode_hostname（app.utils.hostname）────────────────────────────


def test_punycode_decoding_passthrough_for_ascii() -> None:
    assert from_punycode_hostname("example.com") == "example.com"


def test_punycode_decoding_translates_xn_label() -> None:
    # xn--fsq.com is the punycode for 中.com (single CJK char .com)
    decoded = from_punycode_hostname("xn--fsq.com")
    assert decoded.endswith(".com")
    # First label should be a non-ASCII single char (the actual decoded form).
    first = decoded.split(".")[0]
    assert len(first) == 1 and ord(first) > 127


def test_punycode_decoding_handles_invalid_label_gracefully() -> None:
    # Bogus xn-- label that isn't valid punycode → keep original
    assert from_punycode_hostname("xn--!!invalid.com") == "xn--!!invalid.com"


# ─── _extra_block_comment ────────────────────────────────────────────────────


def test_extra_block_comment_is_deterministic() -> None:
    a = fw._extra_block_comment("10.0.0.0/8")
    b = fw._extra_block_comment("10.0.0.0/8")
    assert a == b
    assert a.startswith("SkyLab:block-extra:")


def test_extra_block_comment_differs_per_dest() -> None:
    a = fw._extra_block_comment("10.0.0.0/8")
    b = fw._extra_block_comment("192.168.0.0/16")
    assert a != b
