"""script_policy 與 script_quality_validator 共用的 AST 小工具。"""

from __future__ import annotations

import ast


def call_name(node: ast.AST, aliases: dict[str, str] | None = None) -> str | None:
    """把呼叫目標還原成 ``module.attr`` 形式的名稱（依 import 別名表展開）。"""
    aliases = aliases or {}
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func, aliases)
    return None


def literal_str(node: ast.AST | None) -> str | None:
    """節點是字串常數時回傳其值，否則 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
