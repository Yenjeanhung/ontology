"""权限注册中心：权限点清单、模块→路径前缀映射、接口→权限码解析。

设计要点（对标 Palantir Foundry）：
- 角色是"权限集合"而非层级，角色之间无继承关系；
- 权限点分 `menu`（菜单可见）/ `api`（接口）/ `action`（按钮）三类；
- 接口鉴权只对**写操作**做强制校验（GET 默认放行，由菜单权限控制可见性），
  通过"模块前缀 + HTTP 方法"自动推导，避免 300+ 接口逐个登记。
"""
from __future__ import annotations

from typing import Iterable

# ── 模块 → 一级路径前缀（/api 之后的首段）──
MODULES: list[tuple[str, str, list[str]]] = [
    ("kb", "知识库", ["kb"]),
    ("file", "文件管理", ["files", "upload", "assets", "file-directories", "crawl-jobs", "library"]),
    (
        "ontology", "本体管理",
        [
            "ontology", "ontologies", "ontology-categories", "attribute-templates",
            "shared-properties", "ontology-services", "ontology-functions",
            "ontology-relation-properties", "ontology-service-effects",
            "ontology-service-invocations", "ontology-service-rules",
            "ontology-suggestions", "object-views", "interfaces", "functions",
            "derived-properties", "services", "relations",
        ],
    ),
    ("entity", "实体管理", ["entities", "graph-cleanup"]),
    ("graph", "知识图谱", ["graph", "graph-sync", "graph-analysis"]),
    ("vector", "向量数据", ["vector-records", "vector-files", "vector-search-test", "vector-summary-export"]),
    ("agent", "智能体", ["agent", "agents"]),
    ("workflow", "工作流", ["workflow", "workflows"]),
    ("schedule", "定时管理", ["schedules"]),
    ("query", "知识问答", ["query"]),
    ("config", "系统配置", ["config", "monitor"]),
    ("system", "系统管理", [
        "users", "user-groups", "roles", "permissions", "sessions",
        "audit", "security-settings",
    ]),
]

MODULE_NAMES = {code: name for code, name, _ in MODULES}
PREFIX_TO_MODULE: dict[str, str] = {}
for _code, _name, _prefixes in MODULES:
    for _p in _prefixes:
        PREFIX_TO_MODULE[_p] = _code

# ── 动词 → 权限动作后缀 ──
VERB_TO_ACTION = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
    "GET": "view",
}
ACTION_NAMES = {
    "view": "查看",
    "create": "新增",
    "update": "修改",
    "delete": "删除",
}

# ── 模块专属权限点（除 view/create/update/delete 之外的动作）──
EXTRA_PERMISSIONS: list[tuple[str, str, str, str, str]] = [
    # (code, name, module, type, resource)
    ("kb:query", "知识库检索", "kb", "api", "POST /api/query"),
    ("file:upload", "文件上传", "file", "api", "POST /api/upload/**"),
    ("file:crawl", "联网采集", "file", "api", "POST /api/crawl-jobs**"),
    ("graph:sync", "图迁入", "graph", "api", "POST /api/graph-sync/**"),
    ("graph:analysis", "图计算分析", "graph", "api", "POST /api/graph-analysis/**"),
    ("ontology:version:manage", "本体版本管理", "ontology", "api", "POST /api/ontology-categories/*/versions**"),
    ("ontology:suggestion:review", "本体建议审核", "ontology", "api", "POST /api/ontology-suggestions/**"),
    ("ontology:invoke", "本体动作执行", "ontology", "api", "POST /api/ontology-services/*/invoke"),
    ("workflow:run", "运行工作流", "workflow", "api", "POST /api/workflows/*/run"),
    ("workflow:task:handle", "处理人工任务", "workflow", "api", "POST /api/workflow/human-tasks/**"),
    ("workflow:design", "工作流编排", "workflow", "api", "POST /api/workflow/http-node/test"),
    ("schedule:manage", "调度启停与执行", "schedule", "api", "POST /api/schedules/**"),
    ("vector:export", "向量数据导出", "vector", "action", ""),
    ("config:llm:manage", "模型配置", "config", "api", "POST /api/config/llm**"),
    ("config:monitor:manage", "监控与数据查询", "config", "api", "POST /api/monitor/**"),
    ("system:user:manage", "用户管理", "system", "menu", ""),
    ("system:role:manage", "角色权限管理", "system", "menu", ""),
    ("system:session:manage", "上下线管理", "system", "menu", ""),
    ("system:audit:view", "查看操作日志", "system", "menu", ""),
    ("system:audit:export", "导出操作日志", "system", "action", ""),
    ("system:settings:manage", "安全策略配置", "system", "menu", ""),
]

# ── 显式覆盖规则（优先于"模块前缀 + 动词"推导；越靠前优先级越高）──
# (method, path_pattern, perm_code)
# 说明：GET 默认不强制鉴权，但显式规则可以覆盖（用于日志/用户等敏感只读接口）。
EXPLICIT_RULES: list[tuple[str, str, str]] = [
    # 系统管理（GET 也需校验，防止绕过页面直接调接口）
    ("GET", "/api/audit/logs/export", "system:audit:export"),
    ("GET", "/api/audit/**", "system:audit:view"),
    ("POST", "/api/audit/**", "system:audit:view"),
    ("GET", "/api/security-settings", "system:settings:manage"),
    ("PUT", "/api/security-settings", "system:settings:manage"),
    ("GET", "/api/users/**", "system:user:manage"),
    ("POST", "/api/users", "system:user:manage"),
    ("POST", "/api/users/**", "system:user:manage"),
    ("PUT", "/api/users/**", "system:user:manage"),
    ("PATCH", "/api/users/**", "system:user:manage"),
    ("DELETE", "/api/users/**", "system:user:manage"),
    ("GET", "/api/user-groups/**", "system:user:manage"),
    ("POST", "/api/user-groups", "system:user:manage"),
    ("PUT", "/api/user-groups/**", "system:user:manage"),
    ("DELETE", "/api/user-groups/**", "system:user:manage"),
    ("GET", "/api/roles/**", "system:role:manage"),
    ("POST", "/api/roles", "system:role:manage"),
    ("PUT", "/api/roles/**", "system:role:manage"),
    ("DELETE", "/api/roles/**", "system:role:manage"),
    ("GET", "/api/permissions/**", "system:role:manage"),
    ("POST", "/api/permissions/**", "system:role:manage"),
    ("GET", "/api/sessions/**", "system:session:manage"),
    ("POST", "/api/sessions/**", "system:session:manage"),
    ("DELETE", "/api/sessions/**", "system:session:manage"),
    # 业务写操作
    ("POST", "/api/query", "kb:query"),
    ("POST", "/api/upload/**", "file:upload"),
    ("POST", "/api/assets/upload/chunk", "file:upload"),
    ("POST", "/api/crawl-jobs", "file:crawl"),
    ("POST", "/api/graph-sync/**", "graph:sync"),
    ("POST", "/api/graph-analysis/**", "graph:analysis"),
    ("POST", "/api/ontology-services/*/invoke", "ontology:invoke"),
    ("POST", "/api/ontology-suggestions/**", "ontology:suggestion:review"),
    ("POST", "/api/workflows/*/run", "workflow:run"),
    ("POST", "/api/workflow/human-tasks/**", "workflow:task:handle"),
    ("POST", "/api/workflow/http-node/test", "workflow:design"),
    ("POST", "/api/schedules/validate-cron", "schedule:manage"),
    ("POST", "/api/schedules/preview-next-run", "schedule:manage"),
    ("POST", "/api/schedules/*/toggle", "schedule:manage"),
    ("POST", "/api/schedules/*/run-now", "schedule:manage"),
    ("POST", "/api/monitor/**", "config:monitor:manage"),
]

# ── 预置角色：权限集合（"*" 表示除系统管理外的全部；super_admin 特殊处理）──
_ALL_MODULES = [c for c, _n, _p in MODULES if c != "system"]
_ALL_VIEW = [f"{m}:view" for m in _ALL_MODULES]
_ALL_WRITE = [f"{m}:{a}" for m in _ALL_MODULES for a in ("create", "update", "delete")]

DEFAULT_ROLES: list[dict] = [
    {
        "code": "super_admin", "name": "超级管理员", "is_system": 1, "sort_order": 1,
        "description": "内置角色，拥有全部权限且不参与接口鉴权校验，不可删除",
        "permissions": "*",
    },
    {
        "code": "system_admin", "name": "系统管理员", "is_system": 1, "sort_order": 2,
        "description": "用户/角色/日志/会话/策略全管，业务数据只读",
        "permissions": _ALL_VIEW + [
            "system:user:manage", "system:role:manage", "system:session:manage",
            "system:audit:view", "system:audit:export", "system:settings:manage",
            "config:llm:manage", "config:monitor:manage",
            "kb:query", "workflow:run", "workflow:task:handle",
        ],
    },
    {
        "code": "knowledge_admin", "name": "知识库管理员", "is_system": 1, "sort_order": 3,
        "description": "知识库/文件/实体/图谱/向量全权限",
        "permissions": (
            _ALL_VIEW
            + [f"{m}:{a}" for m in ("kb", "file", "entity", "graph", "vector") for a in ("create", "update", "delete")]
            + ["kb:query", "file:upload", "file:crawl", "graph:sync", "graph:analysis", "vector:export"]
        ),
    },
    {
        "code": "ontology_engineer", "name": "本体工程师", "is_system": 1, "sort_order": 4,
        "description": "本体定义、版本与建议审核全权限",
        "permissions": (
            _ALL_VIEW
            + [f"ontology:{a}" for a in ("create", "update", "delete")]
            + ["ontology:version:manage", "ontology:suggestion:review", "ontology:invoke"]
        ),
    },
    {
        "code": "data_operator", "name": "数据运营", "is_system": 1, "sort_order": 5,
        "description": "文件/知识库/实体增删改，不具备本体定义权限",
        "permissions": (
            _ALL_VIEW
            + [f"{m}:{a}" for m in ("kb", "file", "entity") for a in ("create", "update", "delete")]
            + ["kb:query", "file:upload", "file:crawl"]
        ),
    },
    {
        "code": "analyst", "name": "分析员", "is_system": 1, "sort_order": 6,
        "description": "查询、图谱与工作流运行，以只读为主",
        "permissions": (
            _ALL_VIEW
            + ["kb:query", "workflow:run", "workflow:task:handle", "graph:analysis", "ontology:invoke"]
        ),
    },
    {
        "code": "auditor", "name": "审计员", "is_system": 1, "sort_order": 7,
        "description": "全模块只读，可查看与导出操作日志",
        "permissions": _ALL_VIEW + ["system:audit:view", "system:audit:export"],
    },
    {
        "code": "viewer", "name": "访客", "is_system": 1, "sort_order": 8,
        "description": "全模块只读",
        "permissions": _ALL_VIEW,
    },
]

SUPER_ADMIN_ROLE_CODE = "super_admin"


def _base_permissions() -> list[dict]:
    """模块 × 动词 生成的基础权限点（system 模块用专属码，不生成 CRUD）。"""
    rows: list[dict] = []
    sort = 0
    for code, name, prefixes in MODULES:
        if code == "system":
            continue
        for action in ("view", "create", "update", "delete"):
            sort += 1
            rows.append({
                "code": f"{code}:{action}",
                "name": f"{name}{ACTION_NAMES[action]}",
                "module": code,
                "type": "api",
                "resource": "",
                "is_system": 1,
                "sort_order": sort,
            })
    return rows


def iter_permissions() -> list[dict]:
    """全部权限点（基础 + 专属），供 bootstrap 同步入库。"""
    rows = _base_permissions()
    base = len(rows)
    for idx, (code, name, module, ptype, resource) in enumerate(EXTRA_PERMISSIONS):
        rows.append({
            "code": code,
            "name": name,
            "module": module,
            "type": ptype,
            "resource": resource,
            "is_system": 1,
            "sort_order": base + idx + 1,
        })
    return rows


def module_of_path(path: str) -> str | None:
    """取路径对应的模块 code；不属于任何已知模块时返回 None。"""
    seg = (path or "").lstrip("/").split("/")
    # path 形如 /api/kb/xxx：第 0 段为 api，第 1 段为一级前缀
    prefix = seg[1] if len(seg) > 1 else ""
    return PREFIX_TO_MODULE.get(prefix)


def match_pattern(pattern: str, path: str) -> bool:
    """路径通配匹配：`*` 匹配单段，`**` 匹配剩余任意段。"""
    if not pattern:
        return False
    p_parts = [p for p in pattern.strip("/").split("/") if p != ""]
    a_parts = [p for p in path.strip("/").split("/") if p != ""]
    i = j = 0
    while i < len(p_parts) and j < len(a_parts):
        seg = p_parts[i]
        if seg == "**":
            return True
        if seg != "*" and seg != a_parts[j]:
            return False
        i += 1
        j += 1
    if i == len(p_parts) and j == len(a_parts):
        return True
    # pattern 以 ** 结尾且路径已耗尽
    return i < len(p_parts) and p_parts[i] == "**" and j == len(a_parts)


def resolve(method: str, path: str) -> str | None:
    """把 (method, path) 解析为所需权限码；无需鉴权时返回 None。

    - 先匹配显式覆盖规则（可覆盖 GET，用于敏感只读接口）；
    - GET / HEAD / OPTIONS 未被显式规则命中时返回 None（读操作不强制鉴权，由菜单控制可见性）；
    - 其余按"模块前缀 + HTTP 动词"推导；
    - 未识别模块的写操作返回 None（放行），避免误伤新增业务接口。
    """
    if not path.startswith("/api/"):
        return None
    method = (method or "GET").upper()

    for m, pattern, code in EXPLICIT_RULES:
        if m == method and match_pattern(pattern.lstrip("/"), path.lstrip("/")):
            return code

    if method in ("GET", "HEAD", "OPTIONS"):
        return None

    module = module_of_path(path)
    if not module:
        return None
    action = VERB_TO_ACTION.get(method)
    if not action:
        return None
    return f"{module}:{action}"


def default_role_permissions(codes_by_code: dict[str, str]) -> dict[str, list[str]]:
    """角色 code → 权限码列表（"*" 展开为除 system 模块外的全部权限）。"""
    result: dict[str, list[str]] = {}
    all_codes = [c for c in codes_by_code if not c.startswith("system:")]
    for role in DEFAULT_ROLES:
        perms = role["permissions"]
        if perms == "*":
            result[role["code"]] = list(all_codes)
        else:
            result[role["code"]] = [p for p in perms if p in codes_by_code]
    return result


def iter_default_roles() -> Iterable[dict]:
    return DEFAULT_ROLES
