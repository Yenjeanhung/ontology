"""用户与权限体系单元测试（纯函数，不依赖数据库）。

运行：python -m pytest backend/test/test_auth_permissions.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security import hash_password, verify_password
from services.audit_service import mask_value
from services.permission_registry import match_pattern, module_of_path, resolve
from services.user_service import validate_password


# ── 路径通配 ──

def test_match_pattern():
    assert match_pattern("/api/kb/*", "/api/kb/abc") is True
    assert match_pattern("/api/kb/*", "/api/kb/abc/def") is False
    assert match_pattern("/api/kb/**", "/api/kb/abc/def") is True
    assert match_pattern("/api/users/**", "/api/users") is True
    assert match_pattern("/api/users/**", "/api/users/a1") is True
    assert match_pattern("/api/kb", "/api/kb/abc") is False


def test_module_of_path():
    assert module_of_path("/api/kb/xxx") == "kb"
    assert module_of_path("/api/ontology-categories/1") == "ontology"
    assert module_of_path("/api/users/1") == "system"
    assert module_of_path("/api/sessions") == "system"
    assert module_of_path("/api/unknown-thing") is None


# ── 接口 → 权限码 ──

def test_resolve_write_operations():
    assert resolve("POST", "/api/kb") == "kb:create"
    assert resolve("PUT", "/api/kb/abc") == "kb:update"
    assert resolve("DELETE", "/api/kb/abc") == "kb:delete"
    assert resolve("DELETE", "/api/files/x") == "file:delete"
    assert resolve("POST", "/api/workflows/w1") == "workflow:create"


def test_resolve_get_is_open_by_default():
    """读操作默认不强制鉴权，由菜单权限控制可见性"""
    assert resolve("GET", "/api/kb") is None
    assert resolve("HEAD", "/api/kb") is None
    assert resolve("OPTIONS", "/api/kb") is None


def test_resolve_explicit_rules():
    assert resolve("POST", "/api/query") == "kb:query"
    assert resolve("POST", "/api/workflows/w1/run") == "workflow:run"
    assert resolve("POST", "/api/graph-sync/start") == "graph:sync"
    assert resolve("POST", "/api/schedules/s1/toggle") == "schedule:manage"
    assert resolve("POST", "/api/monitor/database/query") == "config:monitor:manage"


def test_resolve_protects_sensitive_reads():
    """显式规则可以覆盖 GET，防止绕过页面直接调接口"""
    assert resolve("GET", "/api/audit/logs") == "system:audit:view"
    assert resolve("GET", "/api/audit/logs/export") == "system:audit:export"
    assert resolve("GET", "/api/users") == "system:user:manage"
    assert resolve("GET", "/api/sessions") == "system:session:manage"


def test_resolve_ignores_non_api_and_unknown_module():
    assert resolve("POST", "/static/app.js") is None
    assert resolve("POST", "/api/brand-new-module/x") is None


# ── 审计脱敏 ──

def test_mask_value():
    data = {
        "username": "alice",
        "password": "p@ssw0rd",
        "nested": {"api_key": "sk-xxx", "name": "kb"},
        "tokens": [{"token": "abc"}],
    }
    masked = mask_value(data)
    assert masked["username"] == "alice"
    assert masked["password"] == "***"
    assert masked["nested"]["api_key"] == "***"
    assert masked["nested"]["name"] == "kb"
    assert masked["tokens"][0]["token"] == "***"


def test_mask_value_truncates():
    assert mask_value("x" * 3000).endswith("…(已截断)")


# ── 密码 ──

def test_password_hash_roundtrip():
    hashed = hash_password("Str0ngPass")
    assert hashed != "Str0ngPass"
    assert verify_password("Str0ngPass", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("x", "") is False


def test_validate_password_policy():
    # 默认策略：最小 8 位 + 大小写字母和数字
    for bad in ["short1A", "alllowercase1", "NoDigitsHere", "12345678", ""]:
        try:
            validate_password(bad)
        except ValueError:
            continue
        raise AssertionError(f"应被拒绝的密码却通过了：{bad!r}")
    validate_password("Str0ngPass")  # 不抛异常即通过
