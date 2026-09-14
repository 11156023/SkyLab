"""課程環境的連線驗證：重疊的連線必須在存檔時就被擋下。

雙向連線本身就包含反向，再畫一條同 port 的反向單向線並不會多開任何東西，
只會吃掉 6 條的額度、並在拓樸圖上出現兩條意義相同的線。規則同步是以
comment 為 key，所以重疊在防火牆層會自動收斂——這是編輯層的問題。
"""

import pytest

from app.api.routes.course_environments import (
    EnvironmentEdgeIn,
    EnvironmentNodeIn,
    EnvironmentPublicationIn,
    _validate_configuration,
)
from app.exceptions import BadRequestError


def _node(key: str) -> EnvironmentNodeIn:
    return EnvironmentNodeIn(
        node_key=key,
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name=key,
        role="target",
        resource_type="lxc",
        cpu=2,
        memory_mb=2048,
        disk_gb=8,
    )


def _edge(source: str, target: str, *, direction="one_way", protocol="tcp", port=22):
    return EnvironmentEdgeIn(
        source_node_key=source,
        target_node_key=target,
        direction=direction,
        protocol=protocol,
        port=port,
    )


NODES = [_node("web"), _node("db")]


def _validate(edges, publications=None):
    _validate_configuration(None, NODES, edges, publications)


def _publication(node="web", *, port=80, hostname="{student}-web"):
    return EnvironmentPublicationIn(
        node_key=node,
        mode="domain",
        port=port,
        protocol="tcp",
        hostname_prefix=hostname,
        zone_id="zone-1",
    )


# ── 應該被擋下的重疊 ────────────────────────────────────────────────────


def test_bidirectional_covers_the_reverse_one_way() -> None:
    with pytest.raises(BadRequestError):
        _validate([_edge("web", "db", direction="bidirectional"), _edge("db", "web")])


def test_bidirectional_covers_the_same_direction_one_way() -> None:
    with pytest.raises(BadRequestError):
        _validate([_edge("web", "db", direction="bidirectional"), _edge("web", "db")])


def test_two_bidirectionals_in_either_order_are_the_same_thing() -> None:
    with pytest.raises(BadRequestError):
        _validate([
            _edge("web", "db", direction="bidirectional"),
            _edge("db", "web", direction="bidirectional"),
        ])


def test_exact_duplicate_is_still_rejected() -> None:
    with pytest.raises(BadRequestError):
        _validate([_edge("web", "db"), _edge("web", "db")])


def test_legacy_any_protocol_overlaps_everything_between_the_pair() -> None:
    with pytest.raises(BadRequestError):
        _validate([_edge("web", "db", protocol="any", port=None), _edge("web", "db")])


# ── 應該被允許的組合 ────────────────────────────────────────────────────


def test_different_ports_between_the_same_pair_are_fine() -> None:
    _validate([_edge("web", "db", port=80), _edge("web", "db", port=443)])


def test_opposite_one_way_edges_grant_distinct_directions() -> None:
    """兩條相反的單向線各自只開一個方向，沒有重疊。"""
    _validate([_edge("web", "db"), _edge("db", "web")])


def test_different_protocols_do_not_overlap() -> None:
    _validate([_edge("web", "db", protocol="tcp"), _edge("web", "db", protocol="udp")])


def test_edge_pointing_at_an_unknown_node_is_rejected() -> None:
    with pytest.raises(BadRequestError):
        _validate([_edge("web", "cache")])


# ── 對外服務 ────────────────────────────────────────────────────────────


def test_two_ports_cannot_share_one_hostname_template() -> None:
    """一個網址只能指向一個 port；同樣板的第二條在開課時才會撞上網域被占用。"""
    with pytest.raises(BadRequestError):
        _validate([], [_publication(port=80), _publication(port=443)])


def test_distinct_hostname_templates_are_fine() -> None:
    _validate([], [
        _publication(port=80, hostname="{student}-web"),
        _publication(port=443, hostname="{student}-web-tls"),
    ])


def test_same_port_cannot_be_published_twice() -> None:
    with pytest.raises(BadRequestError):
        _validate([], [
            _publication(port=80, hostname="{student}-a"),
            _publication(port=80, hostname="{student}-b"),
        ])


def test_publication_pointing_at_an_unknown_node_is_rejected() -> None:
    with pytest.raises(BadRequestError):
        _validate([], [_publication("cache")])


def test_firewall_only_publications_do_not_need_distinct_hostnames() -> None:
    firewall_only = [
        EnvironmentPublicationIn(node_key="web", mode="firewall_only", port=port)
        for port in (80, 443)
    ]
    _validate([], firewall_only)
