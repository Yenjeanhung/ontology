from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    files = relationship("File", back_populates="kb", cascade="all, delete-orphan")


class FileDirectory(Base):
    __tablename__ = "file_directories"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("file_directories.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    parent = relationship("FileDirectory", remote_side=[id], back_populates="children")
    children = relationship("FileDirectory", back_populates="parent", cascade="all, delete-orphan")
    assets = relationship("FileAsset", back_populates="directory")


class FileAsset(Base):
    __tablename__ = "file_assets"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    directory_id = Column(String, ForeignKey("file_directories.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False, default=0)
    ext = Column(String, default="")
    mime_type = Column(String, default="")
    sha256 = Column(String, default="")
    path = Column(String, nullable=True)
    source_type = Column(String, default="upload")
    source_url = Column(String, nullable=True)
    source_keyword = Column(String, nullable=True)
    sources = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    status = Column(String, default="ready")
    message = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())

    directory = relationship("FileDirectory", back_populates="assets")
    kb_files = relationship("File", back_populates="asset")


class File(Base):
    __tablename__ = "files"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("file_assets.id", ondelete="SET NULL"), nullable=True)
    kb_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False, default=0)
    status = Column(String, default="uploading")
    progress = Column(Integer, default=0)
    message = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)
    path = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    kb = relationship("KnowledgeBase", back_populates="files")
    asset = relationship("FileAsset", back_populates="kb_files")
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    file_id = Column(String, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    embedding_id = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    file = relationship("File", back_populates="chunks")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    keyword = Column(String, nullable=False)
    directory_id = Column(String, ForeignKey("file_directories.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, default="queued")
    progress = Column(Integer, default=0)
    message = Column(String, nullable=True)
    urls = Column(Text, nullable=True)
    file_count = Column(Integer, default=0)
    detail = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    finished_at = Column(String, nullable=True)


# ===== 本体定义层（无外键，逻辑关联由 service 层维护）=====

class OntologyCategory(Base):
    __tablename__ = "ontology_categories"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Ontology(Base):
    __tablename__ = "ontologies"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    color = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyAttribute(Base):
    __tablename__ = "ontology_attributes"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    ontology_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    default_value = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyRelation(Base):
    __tablename__ = "ontology_relations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    description = Column(String, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyRelationConstraint(Base):
    __tablename__ = "ontology_relation_constraints"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    source_ontology_id = Column(String, nullable=False)
    relation_id = Column(String, nullable=False)
    target_ontology_id = Column(String, nullable=False)
    description = Column(String, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class KbOntologyBinding(Base):
    __tablename__ = "kb_ontology_bindings"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    category_id = Column(String, nullable=False)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 本体建议（动态生成 + 审核）=====

class OntologySuggestion(Base):
    __tablename__ = "ontology_suggestions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    file_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="generating")  # generating | ready | approved | rejected
    source_mode = Column(String, nullable=False, default="free_extraction")  # free_extraction | auto_cluster | manual
    suggestion_data = Column(Text, nullable=False, default="{}")  # JSON blob: category/ontologies/relations/constraints/stats
    score = Column(Float, default=0.0)
    review_notes = Column(String, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    reviewed_at = Column(String, nullable=True)
    reviewer = Column(String, nullable=True)


# ===== 属性模板（全局，跨本体类别复用）=====

class OntologyAttributeTemplate(Base):
    __tablename__ = "ontology_attribute_templates"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(String, default="")
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyTemplateAttribute(Base):
    __tablename__ = "ontology_template_attributes"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    template_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    default_value = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyTemplateBinding(Base):
    __tablename__ = "ontology_template_bindings"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    ontology_id = Column(String, nullable=False)
    template_id = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 实体实例层（抽取后生成，无外键）=====

class Entity(Base):
    __tablename__ = "entities"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    ontology_id = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    properties = Column(Text, nullable=True)
    source_file_id = Column(String, nullable=True)
    source_chunk_id = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Relation(Base):
    __tablename__ = "relations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    relation_def_id = Column(String, nullable=False)
    relation_type = Column(String, nullable=False)
    source_entity_id = Column(String, nullable=False)
    target_entity_id = Column(String, nullable=False)
    description = Column(String, default="")
    source_file_id = Column(String, nullable=True)
    source_chunk_id = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 大模型配置（页面配置，多套方案，同一时间仅一条生效）=====

class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="openai")   # openai | anthropic
    api_key = Column(String, default="")
    base_url = Column(String, default="")
    model = Column(String, default="")
    max_tokens = Column(Integer, default=4096)
    temperature = Column(Float, default=0.7)
    is_active = Column(Integer, nullable=False, default=0)        # 1 = 当前生效
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())
