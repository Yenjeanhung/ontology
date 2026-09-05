# -*- coding: utf-8 -*-
"""临时脚本：验证日期实体过滤修复。"""
import sys

sys.path.insert(0, ".")

from services.graph_extraction_service import GraphExtractionService

# 验证1：日期作为关系端点 → 整个关系被丢弃
rel = GraphExtractionService._to_relation(
    {"source_name": "波音737", "source_type": "机型",
     "target_name": "2002-08-29", "target_type": "日期",
     "relation_type": "发布于"},
    {},
)
print("日期端点关系 ->", rel)
assert rel is None

# 验证2：日期经关系端点补建 → 不再补建实体
entities, lookup = [], {}
GraphExtractionService._ensure_entity_from_relation(entities, lookup, "2008-12-05", "日期")
print("补建日期实体 ->", entities)
assert entities == []

# 验证3：正常实体不受影响
GraphExtractionService._ensure_entity_from_relation(entities, lookup, "CFM56发动机", "部件")
print("补建正常实体 ->", [(e.name, e.entity_type) for e in entities])
assert len(entities) == 1

# 验证4：显式日期实体仍被 _to_entity 过滤
ent = GraphExtractionService._to_entity({"name": "2011-02-09", "entity_type": "DATE"})
print("显式日期实体 ->", ent)
assert ent is None

print("\n全部验证通过")
