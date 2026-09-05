-- 数据质量校验：确认新生成的民航维修实体里没有"日期 / 纯数字 / 无语义"噪声实体
\pset pager off

\echo '===== 1) 实体总数 / 关系总数 ====='
SELECT
  (SELECT count(*) FROM entities WHERE kb_id = '72f1ecec567e') AS entities,
  (SELECT count(*) FROM relations WHERE kb_id = '72f1ecec567e') AS relations;

\echo '===== 2) 噪声实体抽查（应为 0 行）====='
SELECT entity_type, name
FROM entities
WHERE kb_id = '72f1ecec567e'
  AND (
       name ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'      -- ISO 日期
    OR name ~ '^[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}'  -- 斜杠日期
    OR name ~ '^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}'-- 点分日期
    OR name ~ '^[0-9]+(\.[0-9]+)?$'              -- 纯数值
    OR name ~ '^[0-9]+(\.[0-9]+)?(辆|个|人|万|亿|%|元|台|次|岁|分|秒|公里|米)$'
    OR name ~ '^https?://'                       -- URL
    OR name ~ '^v?[0-9]+(\.[0-9]+)+$'            -- 版本号
    OR length(name) > 60                         -- 过长疑似整句
  )
LIMIT 30;

\echo '===== 3) 实体名最短/最长（人工可判读性）====='
SELECT min(length(name)) AS min_len,
       round(avg(length(name)), 1) AS avg_len,
       max(length(name)) AS max_len
FROM entities WHERE kb_id = '72f1ecec567e';

\echo '===== 4) 关系类型分布（应全部来自本体关系字典）====='
SELECT r.relation_type, count(*) AS cnt
FROM relations r
WHERE r.kb_id = '72f1ecec567e'
GROUP BY r.relation_type
ORDER BY cnt DESC;

\echo '===== 5) 关系三元组合规性（源本体-关系-目标本体 是否都在约束内）====='
SELECT count(*) AS violating_relations
FROM relations r
JOIN entities se ON se.id = r.source_entity_id
JOIN entities te ON te.id = r.target_entity_id
WHERE r.kb_id = '72f1ecec567e'
  AND NOT EXISTS (
      SELECT 1
      FROM ontology_relation_constraints c
      JOIN ontologies so ON so.id = c.source_ontology_id
      JOIN ontologies to2 ON to2.id = c.target_ontology_id
      JOIN ontology_relations rl ON rl.id = c.relation_id
      WHERE so.name = se.entity_type
        AND rl.name = r.relation_type
        AND to2.name = te.entity_type
  );

\echo '===== 6) 孤立实体（没有任何关系）====='
SELECT count(*) AS orphan_entities
FROM entities e
WHERE e.kb_id = '72f1ecec567e'
  AND NOT EXISTS (
      SELECT 1 FROM relations r
      WHERE r.source_entity_id = e.id OR r.target_entity_id = e.id
  );

\echo '===== 7) 抽样 15 条实体（人工判读）====='
SELECT entity_type, name, left(description, 40) AS descr
FROM entities
WHERE kb_id = '72f1ecec567e'
ORDER BY random() LIMIT 15;
