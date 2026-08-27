"""条件分支规则引擎单元测试。运行：python -m pytest test_rule_engine.py -q"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.workflow_engine import (
    _eval_rule_tree,
    _eval_leaf,
    _rule_preview,
)


def test_simple_gt():
    ctx = {"n3": {"chechang": 5256}}
    rule = {"combinator": "and", "rules": [
        {"field": "{{n3.chechang}}", "operator": ">", "value": "10000"}
    ]}
    assert _eval_rule_tree(rule, ctx) is False
    assert _eval_rule_tree({"combinator": "and", "rules": [
        {"field": "{{n3.chechang}}", "operator": ">", "value": "1000"}
    ]}, ctx) is True


def test_nested_and_or():
    ctx = {"n3": {"chechang": 5256, "height": 1510, "zhouju": 3130}}
    rule = {"combinator": "and", "rules": [
        {"field": "{{n3.chechang}}", "operator": ">", "value": "1000"},
        {"combinator": "or", "rules": [
            {"field": "{{n3.height}}", "operator": ">", "value": "1500"},
            {"field": "{{n3.zhouju}}", "operator": ">", "value": "3000"},
        ]},
    ]}
    # 5256>1000 AND (1510>1500 OR 3130>3000) = True AND (True OR True) = True
    assert _eval_rule_tree(rule, ctx) is True
    # 把 zhouju 改小于 3000
    ctx2 = {"n3": {"chechang": 5256, "height": 1400, "zhouju": 2000}}
    assert _eval_rule_tree(rule, ctx2) is False


def test_negate():
    ctx = {"n3": {"chechang": 5256}}
    rule = {"field": "{{n3.chechang}}", "operator": ">", "value": "1000", "negate": True}
    assert _eval_rule_tree(rule, ctx) is False
    grp = {"combinator": "and", "negate": True, "rules": [
        {"field": "{{n3.chechang}}", "operator": ">", "value": "1000"}
    ]}
    assert _eval_rule_tree(grp, ctx) is False


def test_empty_group_true():
    ctx = {}
    assert _eval_rule_tree({"combinator": "and", "rules": []}, ctx) is True
    assert _eval_rule_tree({"combinator": "or", "rules": []}, ctx) is True


def test_between():
    ctx = {"n3": {"chechang": 5256}}
    assert _eval_leaf(5256, "between", "[1000, 6000]") is True
    assert _eval_leaf(5256, "between", "[1000, 5000]") is False
    assert _eval_leaf(5256, "between", "1000,6000") is True
    assert _eval_leaf(5256, "not_between", "[1000, 5000]") is True


def test_exists():
    # exists 判断值本身是否非 _MISSING；None 视为存在（非缺失）
    assert _eval_leaf(1, "exists", "") is True
    assert _eval_leaf(None, "exists", "") is True
    assert _eval_leaf(0, "exists", "") is True
    # _MISSING 哨兵表示变量缺失
    from services.workflow_engine import _MISSING
    assert _eval_leaf(_MISSING, "exists", "") is False
    assert _eval_leaf(_MISSING, "not_exists", "") is True


def test_type():
    assert _eval_leaf(123, "type", "number") is True
    assert _eval_leaf("abc", "type", "string") is True
    assert _eval_leaf(True, "type", "bool") is True
    assert _eval_leaf([1, 2], "type", "array") is True
    assert _eval_leaf({"x": 1}, "type", "object") is True
    assert _eval_leaf(None, "type", "null") is True


def test_contains_in_regex():
    assert _eval_leaf("hello world", "contains", "world") is True
    assert _eval_leaf("a", "in", "[\"a\", \"b\", \"c\"]") is True
    assert _eval_leaf("abc", "regex", "^a") is True
    assert _eval_leaf("abc", "not_regex", "^z") is True


def test_preview():
    rule = {"combinator": "and", "rules": [
        {"field": "{{n3.chechang}}", "operator": ">", "value": "10000"},
        {"combinator": "or", "rules": [
            {"field": "{{n3.height}}", "operator": ">", "value": "1500"},
            {"field": "{{n3.zhouju}}", "operator": ">", "value": "3000"},
        ]},
    ]}
    assert _rule_preview(rule) == "({{n3.chechang}} > 10000 AND ({{n3.height}} > 1500 OR {{n3.zhouju}} > 3000))"


if __name__ == "__main__":
    test_simple_gt()
    test_nested_and_or()
    test_negate()
    test_empty_group_true()
    test_between()
    test_exists()
    test_type()
    test_contains_in_regex()
    test_preview()
    print("all rule engine tests passed")
