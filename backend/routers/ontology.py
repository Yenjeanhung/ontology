from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import (
    BatchCreateConstraintsRequest,
    BatchCreateOntologiesRequest,
    BatchCreateRelationsRequest,
    BatchSaveAttributesRequest,
    BatchSaveTemplateAttributesRequest,
    BindKbOntologyRequest,
    BindOntologyTemplatesRequest,
    CreateAttributeTemplateRequest,
    CreateOntologyAttributeRequest,
    CreateOntologyCategoryRequest,
    CreateOntologyRelationRequest,
    CreateOntologyRequest,
    CreateRelationConstraintRequest,
    CreateTemplateAttributeRequest,
    UpdateAttributeTemplateRequest,
    UpdateOntologyAttributeRequest,
    UpdateOntologyCategoryRequest,
    UpdateOntologyRelationRequest,
    UpdateOntologyRequest,
    UpdateRelationConstraintRequest,
)
from services.ontology_service import OntologyService

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _bad_request(detail: str):
    return HTTPException(status_code=400, detail=detail)


# ===== 模块一：本体类别 CRUD =====

@router.get("/ontology-categories")
async def list_categories(q: str = "", db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_categories(db, q)


@router.post("/ontology-categories")
async def create_category(req: CreateOntologyCategoryRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.create_category(db, req.name, req.description or "")


@router.get("/ontology-categories/{category_id}")
async def get_category_detail(category_id: str, db: AsyncSession = Depends(get_db)):
    detail = await OntologyService.get_category_detail(db, category_id)
    if not detail:
        raise _nf("Ontology category not found")
    return detail


@router.put("/ontology-categories/{category_id}")
async def update_category(category_id: str, req: UpdateOntologyCategoryRequest, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.update_category(db, category_id, req.name, req.description)
    if not res:
        raise _nf("Ontology category not found")
    return res


@router.delete("/ontology-categories/{category_id}")
async def delete_category(category_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_category(db, category_id):
        raise _nf("Ontology category not found")
    return {"status": "deleted"}


# ===== 模块二：本体管理（本体 + 属性）=====

@router.get("/ontology-categories/{category_id}/ontologies")
async def list_ontologies(category_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_ontologies(db, category_id)


@router.post("/ontology-categories/{category_id}/ontologies/batch")
async def batch_create_ontologies(category_id: str, req: BatchCreateOntologiesRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.batch_create_ontologies(db, category_id, req.ontologies)


@router.post("/ontology-categories/{category_id}/ontologies")
async def create_ontology(category_id: str, req: CreateOntologyRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.create_ontology(
        db, category_id, req.name, req.description or "", req.color, req.sort_order
    )


@router.put("/ontology-categories/{category_id}/ontologies/{ontology_id}")
async def update_ontology(category_id: str, ontology_id: str, req: UpdateOntologyRequest, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.update_ontology(
        db, ontology_id, req.name, req.description, req.color, req.sort_order
    )
    if not res:
        raise _nf("Ontology not found")
    return res


@router.delete("/ontology-categories/{category_id}/ontologies/{ontology_id}")
async def delete_ontology(category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_ontology(db, ontology_id):
        raise _nf("Ontology not found")
    return {"status": "deleted"}


# --- 本体属性 ---

@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/attributes")
async def list_attributes(category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_attributes(db, ontology_id)


@router.post("/ontology-categories/{category_id}/ontologies/{ontology_id}/attributes")
async def create_attribute(category_id: str, ontology_id: str, req: CreateOntologyAttributeRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyService.create_attribute(db, ontology_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.put("/ontology-categories/{category_id}/ontologies/{ontology_id}/attributes")
async def batch_save_attributes(category_id: str, ontology_id: str, req: BatchSaveAttributesRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyService.batch_save_attributes(db, ontology_id, req.attributes)
    except ValueError as e:
        raise _bad_request(str(e))


@router.put("/ontology-categories/{category_id}/ontologies/{ontology_id}/attributes/{attr_id}")
async def update_attribute(category_id: str, ontology_id: str, attr_id: str, req: UpdateOntologyAttributeRequest, db: AsyncSession = Depends(get_db)):
    try:
        res = await OntologyService.update_attribute(db, attr_id, req)
    except ValueError as e:
        raise _bad_request(str(e))
    if not res:
        raise _nf("Attribute not found")
    return res


@router.delete("/ontology-categories/{category_id}/ontologies/{ontology_id}/attributes/{attr_id}")
async def delete_attribute(category_id: str, ontology_id: str, attr_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_attribute(db, attr_id):
        raise _nf("Attribute not found")
    return {"status": "deleted"}


# --- 本体引用模板 + 合并属性 ---

@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/templates")
async def list_ontology_templates(category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_ontology_templates(db, ontology_id)


@router.put("/ontology-categories/{category_id}/ontologies/{ontology_id}/templates")
async def set_ontology_templates(category_id: str, ontology_id: str, req: BindOntologyTemplatesRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.set_ontology_templates(db, ontology_id, req.template_ids)


@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/merged-attributes")
async def get_merged_attributes(category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.get_merged_attributes(db, ontology_id)
    if not res:
        raise _nf("Ontology not found")
    return res


# ===== 模块三：关系字典 CRUD =====

@router.get("/ontology-categories/{category_id}/relations")
async def list_relations(category_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_relations(db, category_id)


@router.post("/ontology-categories/{category_id}/relations/batch")
async def batch_create_relations(category_id: str, req: BatchCreateRelationsRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.batch_create_relations(db, category_id, req.relations)


@router.post("/ontology-categories/{category_id}/relations")
async def create_relation(category_id: str, req: CreateOntologyRelationRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.create_relation(db, category_id, req.name, req.description or "")


@router.put("/ontology-categories/{category_id}/relations/{relation_id}")
async def update_relation(category_id: str, relation_id: str, req: UpdateOntologyRelationRequest, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.update_relation(db, relation_id, req.name, req.description)
    if not res:
        raise _nf("Relation not found")
    return res


@router.delete("/ontology-categories/{category_id}/relations/{relation_id}")
async def delete_relation(category_id: str, relation_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_relation(db, relation_id):
        raise _nf("Relation not found")
    return {"status": "deleted"}


# ===== 模块四：三元组约束 CRUD =====

@router.get("/ontology-categories/{category_id}/constraints")
async def list_constraints(category_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_constraints(db, category_id)


@router.post("/ontology-categories/{category_id}/constraints/batch")
async def batch_create_constraints(category_id: str, req: BatchCreateConstraintsRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.batch_create_constraints(db, category_id, req.constraints)


@router.post("/ontology-categories/{category_id}/constraints")
async def create_constraint(category_id: str, req: CreateRelationConstraintRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.create_constraint(
        db, category_id, req.source_ontology_id, req.relation_id, req.target_ontology_id, req.description or ""
    )


@router.put("/ontology-categories/{category_id}/constraints/{constraint_id}")
async def update_constraint(category_id: str, constraint_id: str, req: UpdateRelationConstraintRequest, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.update_constraint(db, constraint_id, req)
    if not res:
        raise _nf("Constraint not found")
    return res


@router.delete("/ontology-categories/{category_id}/constraints/{constraint_id}")
async def delete_constraint(category_id: str, constraint_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_constraint(db, constraint_id):
        raise _nf("Constraint not found")
    return {"status": "deleted"}


# ===== 模块五：属性模板管理（全局）=====

@router.get("/attribute-templates")
async def list_templates(q: str = "", db: AsyncSession = Depends(get_db)):
    return await OntologyService.list_templates(db, q)


@router.post("/attribute-templates")
async def create_template(req: CreateAttributeTemplateRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyService.create_template(db, req.name, req.description or "")
    except ValueError as e:
        raise _bad_request(str(e))


@router.get("/attribute-templates/{template_id}")
async def get_template_detail(template_id: str, db: AsyncSession = Depends(get_db)):
    res = await OntologyService.get_template_detail(db, template_id)
    if not res:
        raise _nf("Attribute template not found")
    return res


@router.put("/attribute-templates/{template_id}")
async def update_template(template_id: str, req: UpdateAttributeTemplateRequest, db: AsyncSession = Depends(get_db)):
    try:
        res = await OntologyService.update_template(db, template_id, req.name, req.description)
    except ValueError as e:
        raise _bad_request(str(e))
    if not res:
        raise _nf("Attribute template not found")
    return res


@router.delete("/attribute-templates/{template_id}")
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await OntologyService.delete_template(db, template_id)
    if not deleted:
        raise _nf("Attribute template not found or is system built-in")
    return {"status": "deleted"}


# --- 模板属性 ---

@router.get("/attribute-templates/{template_id}/attributes")
async def list_template_attributes(template_id: str, db: AsyncSession = Depends(get_db)):
    detail = await OntologyService.get_template_detail(db, template_id)
    if not detail:
        raise _nf("Attribute template not found")
    return detail["attributes"]


@router.post("/attribute-templates/{template_id}/attributes")
async def create_template_attribute(template_id: str, req: CreateTemplateAttributeRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyService.create_template_attribute(db, template_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.put("/attribute-templates/{template_id}/attributes")
async def batch_save_template_attributes(template_id: str, req: BatchSaveTemplateAttributesRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyService.batch_save_template_attributes(db, template_id, req.attributes)
    except ValueError as e:
        raise _bad_request(str(e))


@router.put("/attribute-templates/{template_id}/attributes/{attr_id}")
async def update_template_attribute(template_id: str, attr_id: str, req: CreateTemplateAttributeRequest, db: AsyncSession = Depends(get_db)):
    # 复用 CreateTemplateAttributeRequest 作为更新体
    from schemas import UpdateOntologyAttributeRequest
    update_req = UpdateOntologyAttributeRequest(
        name=req.name, code=req.code, data_type=req.data_type, description=req.description,
        is_required=req.is_required, default_value=req.default_value, sort_order=req.sort_order,
    )
    try:
        res = await OntologyService.update_template_attribute(db, attr_id, update_req)
    except ValueError as e:
        raise _bad_request(str(e))
    if not res:
        raise _nf("Template attribute not found")
    return res


@router.delete("/attribute-templates/{template_id}/attributes/{attr_id}")
async def delete_template_attribute(template_id: str, attr_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyService.delete_template_attribute(db, attr_id):
        raise _nf("Template attribute not found")
    return {"status": "deleted"}


# ===== 知识库绑定本体类别 =====

@router.get("/kb/{kb_id}/ontology")
async def get_kb_ontology(kb_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyService.get_kb_binding(db, kb_id)


@router.put("/kb/{kb_id}/ontology")
async def bind_kb_ontology(kb_id: str, req: BindKbOntologyRequest, db: AsyncSession = Depends(get_db)):
    return await OntologyService.bind_kb(db, kb_id, req.category_id)


@router.delete("/kb/{kb_id}/ontology")
async def unbind_kb_ontology(kb_id: str, db: AsyncSession = Depends(get_db)):
    await OntologyService.unbind_kb(db, kb_id)
    return {"status": "unbound"}
