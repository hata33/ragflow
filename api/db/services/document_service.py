#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
文档服务模块

本模块提供文档（Document）管理的核心业务逻辑，包括：
- 文档的查询、分页、过滤与统计
- 文档解析任务的创建、进度同步与取消
- 文档删除时的级联清理（任务、chunks、缩略图、知识图谱引用等）
- 文档上传并同步解析（用于对话中直接上传）
- 文档与文件的关联管理
- 文档元数据管理
"""
import asyncio
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from io import BytesIO

import xxhash
from peewee import fn, Case, JOIN

from api.constants import IMG_BASE64_PREFIX, FILE_NAME_LEN_LIMIT
from api.db import PIPELINE_SPECIAL_PROGRESS_FREEZE_TASK_TYPES, FileType, UserTenantRole, CanvasCategory
from api.db.db_models import DB, Document, Knowledgebase, Task, Tenant, UserTenant, File2Document, File, UserCanvas, User
from api.db.db_utils import bulk_insert_into_db
from api.db.services.common_service import CommonService, retry_deadlock_operation
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.doc_metadata_service import DocMetadataService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, get_format_time
from common.constants import LLMType, ParserType, StatusEnum, TaskStatus, SVR_CONSUMER_GROUP_NAME
from rag.nlp import rag_tokenizer, search
from rag.utils.redis_conn import REDIS_CONN
from common.doc_store.doc_store_base import OrderByExpr
from common import settings


class DocumentService(CommonService):
    """
    文档服务类

    封装对 Document 模型的所有 CRUD 和业务逻辑操作。

    职责包括：
    - 文档的查询、分页、过滤与统计
    - 文档解析任务的创建、进度同步与取消
    - 文档删除时的级联清理（任务、chunks、缩略图、知识图谱引用等）
    - 文档上传并同步解析（用于对话中直接上传）

    Attributes:
        model: Document 数据库模型类
    """

    model = Document

    @classmethod
    def get_cls_model_fields(cls):
        """
        返回 Document 模型中需要对外暴露的字段列表

        用于 SELECT 查询的投影，避免查询不必要的字段。

        逻辑说明：
        1. 定义需要对外暴露的字段列表
        2. 包含文档的基本信息、解析配置、状态、时间戳等
        3. 用于构建查询时的 SELECT 子句

        :return: 字段列表
        """
        return [
            cls.model.id,                    # 文档 ID
            cls.model.thumbnail,             # 缩略图
            cls.model.kb_id,                 # 知识库 ID
            cls.model.parser_id,             # 解析器 ID
            cls.model.pipeline_id,           # 数据流（Canvas）ID
            cls.model.parser_config,         # 解析器配置
            cls.model.source_type,           # 来源类型
            cls.model.type,                  # 文档类型
            cls.model.created_by,            # 创建者
            cls.model.name,                  # 文档名称
            cls.model.location,              # 存储位置
            cls.model.size,                  # 文件大小
            cls.model.token_num,             # token 数量
            cls.model.chunk_num,             # chunk 数量
            cls.model.progress,              # 解析进度
            cls.model.progress_msg,          # 进度消息
            cls.model.process_begin_at,      # 处理开始时间
            cls.model.process_duration,      # 处理时长
            cls.model.suffix,                # 文件后缀
            cls.model.run,                   # 运行状态
            cls.model.status,                # 状态
            cls.model.create_time,           # 创建时间
            cls.model.create_date,           # 创建日期
            cls.model.update_time,           # 更新时间
            cls.model.update_date,           # 更新日期
        ]

    @classmethod
    @DB.connection_context()
    def get_list(cls, kb_id, page_number, items_per_page, orderby, desc, keywords, id, name, suffix=None, run=None, doc_ids=None):
        """
        根据知识库 ID 分页查询文档列表

        通过 JOIN File2Document、File、UserCanvas 表获取关联信息，
        支持按 id、name、keywords、doc_ids、suffix、run 等条件过滤。

        逻辑说明：
        1. 构建多表 JOIN 查询（Document -> File2Document -> File -> UserCanvas）
        2. 应用各种过滤条件
        3. 处理排序
        4. 分页查询
        5. 批量查询元数据并合并到结果中

        :param kb_id: 知识库 ID
        :param page_number: 页码（从 1 开始）
        :param items_per_page: 每页数量
        :param orderby: 排序字段
        :param desc: 是否降序
        :param keywords: 关键词（模糊匹配文档名）
        :param id: 文档 ID（精确匹配）
        :param name: 文档名称（精确匹配）
        :param suffix: 文件后缀列表（如 [".pdf", ".docx"]）
        :param run: 运行状态列表
        :param doc_ids: 文档 ID 列表
        :return: (文档列表, 总数)
        """
        # 获取要查询的字段列表
        fields = cls.get_cls_model_fields()

        # 构建多表 JOIN 查询
        # Document -> File2Document -> File -> UserCanvas（仅 DataFlow 类型）
        docs = (
            cls.model.select(*[*fields, UserCanvas.title])  # 选择文档字段和 Canvas 标题
            .join(File2Document, on=(File2Document.document_id == cls.model.id))  # JOIN 文档-文件关联表
            .join(File, on=(File.id == File2Document.file_id))  # JOIN 文件表
            .join(UserCanvas, on=((cls.model.pipeline_id == UserCanvas.id) & (UserCanvas.canvas_category == CanvasCategory.DataFlow.value)), join_type=JOIN.LEFT_OUTER)  # LEFT JOIN Canvas 表（仅 DataFlow 类型）
            .where(cls.model.kb_id == kb_id)  # 过滤知识库
        )

        # 应用过滤条件
        if id:
            # 按 ID 精确匹配
            docs = docs.where(cls.model.id == id)
        if name:
            # 按名称精确匹配
            docs = docs.where(cls.model.name == name)
        if keywords:
            # 按关键词模糊匹配（忽略大小写）
            docs = docs.where(fn.LOWER(cls.model.name).contains(keywords.lower()))
        if doc_ids:
            # 按 ID 列表过滤
            docs = docs.where(cls.model.id.in_(doc_ids))
        if suffix:
            # 按文件后缀过滤
            docs = docs.where(cls.model.suffix.in_(suffix))
        if run:
            # 按运行状态过滤
            docs = docs.where(cls.model.run.in_(run))

        # 应用排序
        if desc:
            docs = docs.order_by(cls.model.getter_by(orderby).desc())
        else:
            docs = docs.order_by(cls.model.getter_by(orderby).asc())

        # 获取总数（在分页前）
        count = docs.count()

        # 应用分页
        docs = docs.paginate(page_number, items_per_page)

        # 转换为字典列表
        docs_list = list(docs.dicts())

        # 批量查询当前页文档的元数据并合并到结果中
        doc_ids_on_page = [doc["id"] for doc in docs_list]
        metadata_map = DocMetadataService.get_metadata_for_documents(doc_ids_on_page, kb_id) if doc_ids_on_page else {}
        for doc in docs_list:
            doc["meta_fields"] = metadata_map.get(doc["id"], {})

        return docs_list, count

    @classmethod
    @DB.connection_context()
    def check_doc_health(cls, tenant_id: str, filename):
        """
        检查文档上传前的健康约束条件

        验证用户文件数上限和文件名长度限制。

        逻辑说明：
        1. 获取环境变量配置的最大文件数限制
        2. 检查当前用户的文档数是否超过限制
        3. 检查文件名长度是否超过限制

        :param tenant_id: 租户 ID
        :param filename: 文件名
        :return: True
        :raises RuntimeError: 超过文件数上限或文件名长度限制
        """
        import os

        # 获取最大文件数配置（默认为 0 表示不限制）
        MAX_FILE_NUM_PER_USER = int(os.environ.get("MAX_FILE_NUM_PER_USER", 0))

        # 检查是否超过文件数上限
        if 0 < MAX_FILE_NUM_PER_USER <= DocumentService.get_doc_count(tenant_id):
            raise RuntimeError("Exceed the maximum file number of a free user!")

        # 检查文件名长度（按 UTF-8 编码字节数计算）
        if len(filename.encode("utf-8")) > FILE_NAME_LEN_LIMIT:
            raise RuntimeError("Exceed the maximum length of file name!")

        return True

    @classmethod
    @DB.connection_context()
    def get_by_kb_id(cls, kb_id, page_number, items_per_page, orderby, desc, keywords, run_status, types, suffix, doc_ids=None, return_empty_metadata=False):
        """
        按知识库 ID 查询文档，支持多条件过滤和分页

        相比 get_list，此方法额外关联了 pipeline_name 和创建者昵称。

        逻辑说明：
        1. 构建多表 JOIN 查询（Document -> File2Document -> File -> UserCanvas -> User）
        2. 应用过滤条件
        3. 如果 return_empty_metadata=True，只返回没有元数据的文档
        4. 处理排序和分页
        5. 查询并合并元数据

        :param kb_id: 知识库 ID
        :param page_number: 页码
        :param items_per_page: 每页数量
        :param orderby: 排序字段
        :param desc: 是否降序
        :param keywords: 关键词
        :param run_status: 运行状态列表
        :param types: 文档类型列表
        :param suffix: 文件后缀列表
        :param doc_ids: 文档 ID 列表
        :param return_empty_metadata: 是否只返回没有元数据的文档
        :return: (文档列表, 总数)
        """
        fields = cls.get_cls_model_fields()

        # 根据是否有关键词选择不同的查询路径
        if keywords:
            # 有关键词时，需要使用 LOWER 函数进行模糊匹配
            docs = (
                cls.model.select(*[*fields, UserCanvas.title.alias("pipeline_name"), User.nickname])
                .join(File2Document, on=(File2Document.document_id == cls.model.id))
                .join(File, on=(File.id == File2Document.file_id))
                .join(UserCanvas, on=(cls.model.pipeline_id == UserCanvas.id), join_type=JOIN.LEFT_OUTER)
                .join(User, on=(cls.model.created_by == User.id), join_type=JOIN.LEFT_OUTER)
                .where((cls.model.kb_id == kb_id), (fn.LOWER(cls.model.name).contains(keywords.lower())))
            )
        else:
            # 没有关键词时，直接查询
            docs = (
                cls.model.select(*[*fields, UserCanvas.title.alias("pipeline_name"), User.nickname])
                .join(File2Document, on=(File2Document.document_id == cls.model.id))
                .join(UserCanvas, on=(cls.model.pipeline_id == UserCanvas.id), join_type=JOIN.LEFT_OUTER)
                .join(File, on=(File.id == File2Document.file_id))
                .join(User, on=(cls.model.created_by == User.id), join_type=JOIN.LEFT_OUTER)
                .where(cls.model.kb_id == kb_id)
            )

        # 应用过滤条件
        if doc_ids:
            docs = docs.where(cls.model.id.in_(doc_ids))
        if run_status:
            docs = docs.where(cls.model.run.in_(run_status))
        if types:
            docs = docs.where(cls.model.type.in_(types))
        if suffix:
            docs = docs.where(cls.model.suffix.in_(suffix))

        metadata_map = {}
        if return_empty_metadata:
            # 获取所有有元数据的文档 ID，然后排除它们，只返回无元数据的文档
            metadata_map = DocMetadataService.get_metadata_for_documents(None, kb_id)
            doc_ids_with_metadata = set(metadata_map.keys())
            if doc_ids_with_metadata:
                docs = docs.where(cls.model.id.not_in(doc_ids_with_metadata))

        # 获取总数
        count = docs.count()

        # 应用排序
        if desc:
            docs = docs.order_by(cls.model.getter_by(orderby).desc())
        else:
            docs = docs.order_by(cls.model.getter_by(orderby).asc())

        # 应用分页
        if page_number and items_per_page:
            docs = docs.paginate(page_number, items_per_page)

        # 转换为字典列表
        docs_list = list(docs.dicts())

        # 添加元数据字段
        if return_empty_metadata:
            for doc in docs_list:
                doc["meta_fields"] = {}
        else:
            doc_ids_on_page = [doc["id"] for doc in docs_list]
            metadata_map = DocMetadataService.get_metadata_for_documents(doc_ids_on_page, kb_id) if doc_ids_on_page else {}
            for doc in docs_list:
                doc["meta_fields"] = metadata_map.get(doc["id"], {})

        return docs_list, count

    @classmethod
    @DB.connection_context()
    def get_filter_by_kb_id(cls, kb_id, keywords, run_status, types, suffix):
        """
        获取知识库文档的过滤条件统计

        返回文档的后缀、运行状态、元数据值的统计信息。

        逻辑说明：
        1. 查询符合条件的文档（只查询必要字段）
        2. 获取总数
        3. 遍历文档，统计各种过滤条件的值及其出现次数
        4. 查询文档元数据并统计元数据值
        5. 返回统计结果和总数

        返回格式：
        {
            "suffix": {"pdf": 5, "docx": 3},
            "run_status": {"1": 2, "3": 6},  # 1=RUNNING, 3=DONE
            "metadata": {
                "author": {"张三": 2, "李四": 1},
                "category": {"技术": 3}
            }
        }

        :param kb_id: 知识库 ID
        :param keywords: 关键词
        :param run_status: 运行状态列表
        :param types: 文档类型列表
        :param suffix: 文件后缀列表
        :return: (统计字典, 总数)
        """
        fields = cls.get_cls_model_fields()

        # 根据是否有关键词构建查询
        if keywords:
            query = (
                cls.model.select(*fields)
                .join(File2Document, on=(File2Document.document_id == cls.model.id))
                .join(File, on=(File.id == File2Document.file_id))
                .where((cls.model.kb_id == kb_id), (fn.LOWER(cls.model.name).contains(keywords.lower())))
            )
        else:
            query = (
                cls.model.select(*fields)
                .join(File2Document, on=(File2Document.document_id == cls.model.id))
                .join(File, on=(File.id == File2Document.file_id))
                .where(cls.model.kb_id == kb_id)
            )

        # 应用过滤条件
        if run_status:
            query = query.where(cls.model.run.in_(run_status))
        if types:
            query = query.where(cls.model.type.in_(types))
        if suffix:
            query = query.where(cls.model.suffix.in_(suffix))

        # 只查询必要的统计字段
        rows = query.select(cls.model.run, cls.model.suffix, cls.model.id)
        total = rows.count()

        # 初始化统计计数器
        suffix_counter = {}
        run_status_counter = {}
        metadata_counter = {}
        empty_metadata_count = 0

        # 收集文档 ID
        doc_ids = [row.id for row in rows]

        # 查询文档元数据
        metadata = {}
        if doc_ids:
            try:
                metadata = DocMetadataService.get_metadata_for_documents(doc_ids, kb_id)
            except Exception as e:
                logging.warning(f"Failed to fetch metadata from ES/Infinity: {e}")

        # 遍历文档进行统计
        for row in rows:
            # 统计文件后缀
            suffix_counter[row.suffix] = suffix_counter.get(row.suffix, 0) + 1

            # 统计运行状态
            run_status_counter[str(row.run)] = run_status_counter.get(str(row.run), 0) + 1

            # 统计元数据
            meta_fields = metadata.get(row.id, {})
            if not meta_fields:
                empty_metadata_count += 1
                continue

            has_valid_meta = False
            for key, value in meta_fields.items():
                # 处理元数据值（可能是列表或单个值）
                values = value if isinstance(value, list) else [value]
                for vv in values:
                    # 跳过空值
                    if vv is None:
                        continue
                    if isinstance(vv, str) and not vv.strip():
                        continue

                    # 统计元数据值
                    sv = str(vv)
                    if key not in metadata_counter:
                        metadata_counter[key] = {}
                    metadata_counter[key][sv] = metadata_counter[key].get(sv, 0) + 1
                    has_valid_meta = True

            # 如果没有有效的元数据，计入空元数据计数
            if not has_valid_meta:
                empty_metadata_count += 1

        # 添加空元数据统计
        metadata_counter["empty_metadata"] = {"true": empty_metadata_count}

        return {
            "suffix": suffix_counter,
            "run_status": run_status_counter,
            "metadata": metadata_counter,
        }, total

    @classmethod
    @DB.connection_context()
    def get_parsing_status_by_kb_ids(cls, kb_ids: list[str]) -> dict[str, dict[str, int]]:
        """
        按知识库聚合文档解析状态统计

        对于每个知识库，统计各运行状态的文档数：
        - unstart_count  (run == "0")
        - running_count   (run == "1")
        - cancel_count    (run == "2")
        - done_count      (run == "3")
        - fail_count      (run == "4")

        逻辑说明：
        1. 如果 kb_ids 为空，返回空字典
        2. 初始化结果字典（每个 kb_id 都有所有状态的初始值 0）
        3. 执行 GROUP BY 查询，按 kb_id 和 run 分组统计
        4. 将查询结果填充到结果字典中

        :param kb_ids: 知识库 ID 列表
        :return: 字典，格式如 {"kb-abc": {"unstart_count": 10, "running_count": 2, ...}, ...}
        """
        # 如果没有知识库 ID，返回空字典
        if not kb_ids:
            return {}

        # 运行状态到字段名的映射
        status_field_map = {
            TaskStatus.UNSTART.value: "unstart_count",
            TaskStatus.RUNNING.value: "running_count",
            TaskStatus.CANCEL.value: "cancel_count",
            TaskStatus.DONE.value: "done_count",
            TaskStatus.FAIL.value: "fail_count",
        }

        # 初始化结果字典（所有状态的初始值为 0）
        empty_status = {v: 0 for v in status_field_map.values()}
        result: dict[str, dict[str, int]] = {kb_id: dict(empty_status) for kb_id in kb_ids}

        # 执行 GROUP BY 查询
        rows = (
            cls.model.select(
                cls.model.kb_id,
                cls.model.run,
                fn.COUNT(cls.model.id).alias("cnt"),
            )
            .where(cls.model.kb_id.in_(kb_ids))
            .group_by(cls.model.kb_id, cls.model.run)
            .dicts()
        )

        # 填充查询结果
        for row in rows:
            kb_id = row["kb_id"]
            run_val = str(row["run"])
            field_name = status_field_map.get(run_val)
            if field_name and kb_id in result:
                result[kb_id][field_name] = int(row["cnt"])

        return result

    @classmethod
    @DB.connection_context()
    def count_by_kb_id(cls, kb_id, keywords, run_status, types):
        """
        统计知识库下的文档数量

        逻辑说明：
        1. 构建基础查询
        2. 应用过滤条件（关键词、运行状态、文档类型）
        3. 返回计数

        :param kb_id: 知识库 ID
        :param keywords: 关键词
        :param run_status: 运行状态列表
        :param types: 文档类型列表
        :return: 文档数量
        """
        # 根据是否有关键词构建查询
        if keywords:
            docs = cls.model.select().where(
                (cls.model.kb_id == kb_id),
                (fn.LOWER(cls.model.name).contains(keywords.lower()))
            )
        else:
            docs = cls.model.select().where(cls.model.kb_id == kb_id)

        # 应用过滤条件
        if run_status:
            docs = docs.where(cls.model.run.in_(run_status))
        if types:
            docs = docs.where(cls.model.type.in_(types))

        # 返回计数
        count = docs.count()
        return count

    @classmethod
    @DB.connection_context()
    def get_total_size_by_kb_id(cls, kb_id, keywords="", run_status=[], types=[]):
        """
        获取知识库下文档的总大小

        逻辑说明：
        1. 使用 SUM 聚合函数计算文件大小总和
        2. 使用 COALESCE 处理 NULL 值（返回 0）
        3. 应用过滤条件
        4. 返回总大小（字节数）

        :param kb_id: 知识库 ID
        :param keywords: 关键词
        :param run_status: 运行状态列表
        :param types: 文档类型列表
        :return: 总大小（字节数）
        """
        # 构建查询：使用 COALESCE 确保 NULL 返回 0
        query = cls.model.select(fn.COALESCE(fn.SUM(cls.model.size), 0)).where(cls.model.kb_id == kb_id)

        # 应用过滤条件
        if keywords:
            query = query.where(fn.LOWER(cls.model.name).contains(keywords.lower()))
        if run_status:
            query = query.where(cls.model.run.in_(run_status))
        if types:
            query = query.where(cls.model.type.in_(types))

        # 返回总大小
        return int(query.scalar()) or 0

    @classmethod
    @DB.connection_context()
    def get_all_doc_ids_by_kb_ids(cls, kb_ids):
        """
        获取多个知识库的所有文档 ID

        使用分批查询避免深分页性能问题。

        逻辑说明：
        1. 选择要查询的字段（id, kb_id）
        2. 按 kb_id 过滤，按创建时间升序排序
        3. 分批查询（每批 100 条）
        4. 合并所有批次的结果

        :param kb_ids: 知识库 ID 列表
        :return: 文档 ID 列表
        """
        fields = [cls.model.id, cls.model.kb_id]
        docs = cls.model.select(*fields).where(cls.model.kb_id.in_(kb_ids))
        docs.order_by(cls.model.create_time.asc())

        # 分批查询（避免深分页性能问题）
        offset, limit = 0, 100
        res = []
        while True:
            doc_batch = docs.offset(offset).limit(limit)
            _temp = list(doc_batch.dicts())
            if not _temp:
                break
            res.extend(_temp)
            offset += limit

        return res

    @classmethod
    @DB.connection_context()
    def list_doc_headers_by_kb_and_source_type(cls, kb_id, source_type, page_size=500):
        """
        按知识库和来源类型列出文档的基本信息

        逻辑说明：
        1. 选择基本字段（id, kb_id, source_type, name）
        2. 按知识库和来源类型过滤
        3. 按创建时间升序排序
        4. 分批查询（每批 500 条）

        :param kb_id: 知识库 ID
        :param source_type: 来源类型
        :param page_size: 每批大小
        :return: 文档列表
        """
        fields = [cls.model.id, cls.model.kb_id, cls.model.source_type, cls.model.name]
        docs = cls.model.select(*fields).where(
            cls.model.kb_id == kb_id,
            cls.model.source_type == source_type,
        ).order_by(cls.model.create_time.asc())

        # 分批查询
        offset = 0
        res = []
        while True:
            doc_batch = docs.offset(offset).limit(page_size)
            _temp = list(doc_batch.dicts())
            if not _temp:
                break
            res.extend(_temp)
            offset += page_size

        return res

    @classmethod
    @DB.connection_context()
    def get_all_docs_by_creator_id(cls, creator_id):
        """
        获取用户创建的所有文档

        逻辑说明：
        1. 选择字段并关联知识库获取租户 ID
        2. 按创建者过滤
        3. 按创建时间升序排序
        4. 分批查询（每批 100 条）

        :param creator_id: 创建者 ID
        :return: 文档列表（包含 token_num, chunk_num）
        """
        fields = [cls.model.id, cls.model.kb_id, cls.model.token_num, cls.model.chunk_num, Knowledgebase.tenant_id]
        docs = cls.model.select(*fields).join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id)).where(cls.model.created_by == creator_id)
        docs.order_by(cls.model.create_time.asc())

        # 分批查询（避免深分页性能问题）
        offset, limit = 0, 100
        res = []
        while True:
            doc_batch = docs.offset(offset).limit(limit)
            _temp = list(doc_batch.dicts())
            if not _temp:
                break
            res.extend(_temp)
            offset += limit

        return res

    @classmethod
    @DB.connection_context()
    def insert(cls, doc):
        """
        插入文档记录，同时原子性地递增对应知识库的 doc_num 计数

        逻辑说明：
        1. 调用 save 方法插入文档记录
        2. 如果插入失败，抛出异常
        3. 原子性地递增知识库的文档计数
        4. 如果递增失败，抛出异常
        5. 返回创建的文档对象

        :param doc: 文档字典
        :return: 文档对象
        :raises RuntimeError: 插入失败时
        """
        # 插入文档记录
        if not cls.save(**doc):
            raise RuntimeError("Database error (Document)!")

        # 原子性地递增知识库的文档计数
        if not KnowledgebaseService.atomic_increase_doc_num_by_id(doc["kb_id"]):
            raise RuntimeError("Database error (Knowledgebase)!")

        return Document(**doc)

    @classmethod
    @DB.connection_context()
    def remove_document(cls, doc, tenant_id):
        """
        完整删除一个文档，级联清理所有关联资源

        执行顺序：
        1. 从数据库删除文档记录并更新 KB 计数器
        2. 取消该文档所有正在运行的任务（通过 Redis 设置取消标记）
        3. 从数据库删除该文档的所有任务记录
        4. 删除文档关联的 chunk 图片（对象存储）
        5. 删除文档缩略图（对象存储）
        6. 从 doc store (ES/Infinity) 删除所有 chunks
        7. 删除文档元数据
        8. 清理知识图谱中对该文档的引用

        每个步骤独立 try/except，确保单步失败不阻塞后续清理。

        逻辑说明：
        1. 先删除数据库中的文档记录并更新知识库计数
        2. 通过 Redis 设置取消标记来取消正在运行的任务
        3. 删除数据库中的任务记录
        4. 从对象存储删除 chunk 图片
        5. 从对象存储删除文档缩略图
        6. 从文档存储（ES/Infinity）删除所有 chunks
        7. 删除文档元数据
        8. 清理知识图谱中的引用

        :param doc: 文档对象
        :param tenant_id: 租户 ID
        :return: True
        """
        from api.db.services.task_service import TaskService, cancel_all_task_of

        # 步骤 1: 删除文档记录并更新知识库计数（原子操作）
        if not cls.delete_document_and_update_kb_counts(doc.id):
            # 文档已被并发请求删除，直接返回（幂等）
            return True

        # 步骤 2: 取消所有正在运行的任务（通过 Redis 设置取消标记）
        try:
            cancel_all_task_of(doc.id)
            logging.info(f"Cancelled all tasks for document {doc.id}")
        except Exception as e:
            logging.warning(f"Failed to cancel tasks for document {doc.id}: {e}")

        # 步骤 3: 从数据库删除任务记录
        try:
            TaskService.filter_delete([Task.doc_id == doc.id])
        except Exception as e:
            logging.warning(f"Failed to delete tasks for document {doc.id}: {e}")

        # 步骤 4: 删除 chunk 图片（非关键操作，记录后继续）
        try:
            cls.delete_chunk_images(doc, tenant_id)
        except Exception as e:
            logging.warning(f"Failed to delete chunk images for document {doc.id}: {e}")

        # 步骤 5: 删除缩略图（非关键操作，记录后继续）
        try:
            # 只删除对象存储中的缩略图（不包括 base64 格式的）
            if doc.thumbnail and not doc.thumbnail.startswith(IMG_BASE64_PREFIX):
                if settings.STORAGE_IMPL.obj_exist(doc.kb_id, doc.thumbnail):
                    settings.STORAGE_IMPL.rm(doc.kb_id, doc.thumbnail)
        except Exception as e:
            logging.warning(f"Failed to delete thumbnail for document {doc.id}: {e}")

        # 步骤 6: 从文档存储删除所有 chunks（关键操作，记录错误）
        try:
            settings.docStoreConn.delete({"doc_id": doc.id}, search.index_name(tenant_id), doc.kb_id)
        except Exception as e:
            logging.error(f"Failed to delete chunks from doc store for document {doc.id}: {e}")

        # 步骤 7: 删除文档元数据（非关键操作，记录后继续）
        try:
            DocMetadataService.delete_document_metadata(doc.id, doc.kb_id, tenant_id)
        except Exception as e:
            logging.warning(f"Failed to delete metadata for document {doc.id}: {e}")

        # 步骤 8: 清理知识图谱引用（非关键操作，记录后继续）
        try:
            # 查询包含该文档引用的知识图谱
            graph_source = settings.docStoreConn.get_fields(
                settings.docStoreConn.search(["source_id"], [], {"kb_id": doc.kb_id, "knowledge_graph_kwd": ["graph"]}, [], OrderByExpr(), 0, 1, search.index_name(tenant_id), [doc.kb_id]),
                ["source_id"],
            )

            # 如果知识图谱引用了该文档，则清理引用
            if len(graph_source) > 0 and doc.id in list(graph_source.values())[0]["source_id"]:
                # 移除实体、关系等中的 source_id 引用
                settings.docStoreConn.update(
                    {"kb_id": doc.kb_id, "knowledge_graph_kwd": ["entity", "relation", "graph", "subgraph", "community_report"], "source_id": doc.id},
                    {"remove": {"source_id": doc.id}},
                    search.index_name(tenant_id),
                    doc.kb_id,
                )
                # 标记知识图谱为已修改
                settings.docStoreConn.update({"kb_id": doc.kb_id, "knowledge_graph_kwd": ["graph"]}, {"removed_kwd": "Y"}, search.index_name(tenant_id), doc.kb_id)
                # 删除没有 source_id 的孤立节点
                settings.docStoreConn.delete(
                    {"kb_id": doc.kb_id, "knowledge_graph_kwd": ["entity", "relation", "graph", "subgraph", "community_report"], "must_not": {"exists": "source_id"}},
                    search.index_name(tenant_id),
                    doc.kb_id,
                )
        except Exception as e:
            logging.warning(f"Failed to cleanup knowledge graph for document {doc.id}: {e}")

        return True

    @classmethod
    @DB.connection_context()
    def delete_chunk_images(cls, doc, tenant_id):
        """
        分页删除文档所有 chunk 关联的图片（从对象存储中移除）

        逻辑说明：
        1. 初始化分页参数
        2. 循环查询文档的所有 chunks（每批 1000 条）
        3. 对于每个 chunk，如果有图片，从对象存储删除
        4. 直到没有更多 chunks

        :param doc: 文档对象
        :param tenant_id: 租户 ID
        """
        page = 0
        page_size = 1000

        while True:
            # 查询一批 chunks（只查询 img_id 字段）
            chunks = settings.docStoreConn.search(
                ["img_id"], [], {"doc_id": doc.id}, [], OrderByExpr(),
                page * page_size, page_size, search.index_name(tenant_id), [doc.kb_id]
            )

            # 获取 chunk ID 列表
            chunk_ids = settings.docStoreConn.get_doc_ids(chunks)

            # 如果没有更多 chunks，退出循环
            if not chunk_ids:
                break

            # 删除每个 chunk 的图片
            for cid in chunk_ids:
                if settings.STORAGE_IMPL.obj_exist(doc.kb_id, cid):
                    settings.STORAGE_IMPL.rm(doc.kb_id, cid)

            page += 1

    @classmethod
    @DB.connection_context()
    def get_newly_uploaded(cls):
        """
        获取最近 10 分钟内新上传且尚未开始解析的文档列表

        用于后台任务调度器拉取待处理文档。

        查询条件：
        - 状态 VALID
        - 非虚拟文件
        - 进度为 0（未开始）
        - 运行状态为 RUNNING
        - 更新时间在 10 分钟内

        逻辑说明：
        1. 选择必要的字段
        2. 关联知识库和租户表
        3. 应用过滤条件
        4. 按更新时间升序排序（优先处理较早的文档）

        :return: 文档列表
        """
        fields = [
            cls.model.id,
            cls.model.kb_id,
            cls.model.parser_id,
            cls.model.parser_config,
            cls.model.name,
            cls.model.type,
            cls.model.location,
            cls.model.size,
            Knowledgebase.tenant_id,
            Tenant.embd_id,
            Tenant.img2txt_id,
            Tenant.asr_id,
            cls.model.update_time,
        ]

        docs = (
            cls.model.select(*fields)
            .join(Knowledgebase, on=(cls.model.kb_id == Knowledgebase.id))
            .join(Tenant, on=(Knowledgebase.tenant_id == Tenant.id))
            .where(
                cls.model.status == StatusEnum.VALID.value,              # 状态有效
                ~(cls.model.type == FileType.VIRTUAL.value),             # 非虚拟文件
                cls.model.progress == 0,                                  # 进度为 0（未开始）
                cls.model.update_time >= current_timestamp() - 1000 * 600,  # 10分钟内
                cls.model.run == TaskStatus.RUNNING.value,                # 运行状态为 RUNNING
            )
            .order_by(cls.model.update_time.asc())  # 按更新时间升序
        )

        return list(docs.dicts())

    @classmethod
    @DB.connection_context()
    def get_unfinished_docs(cls):
        """
        获取所有未完成解析的文档

        包括：
        - 进度在 0~1 之间的文档
        - 仍有未完成任务的文档
        - 解析失败但有未失败任务（可重试）的文档（含 GraphRAG/RAPTOR/Mindmap）

        逻辑说明：
        1. 构建子查询：获取有未完成任务的任务的文档 ID
        2. 构建子查询：获取有非失败任务的任务的文档 ID
        3. 构建主查询：满足以下任一条件的文档
           - 进度在 0~1 之间
           - 有未完成的任务
           - 进度为 -1（失败）但有非失败任务
        4. 排除已取消的文档

        :return: 文档列表
        """
        fields = [
            cls.model.id,
            cls.model.process_begin_at,
            cls.model.parser_config,
            cls.model.progress_msg,
            cls.model.run,
            cls.model.parser_id
        ]

        # 子查询：获取有未完成任务（进度 0~1）的文档 ID
        unfinished_task_query = Task.select(Task.doc_id).where(
            (Task.progress >= 0) & (Task.progress < 1)
        )

        # 子查询：获取有非失败任务（进度 >= 0）的文档 ID
        docs_with_non_failed_tasks = Task.select(Task.doc_id).where(Task.progress >= 0).distinct()

        # 主查询：获取未完成的文档
        docs = cls.model.select(*fields).where(
            cls.model.status == StatusEnum.VALID.value,              # 状态有效
            ~(cls.model.type == FileType.VIRTUAL.value),             # 非虚拟文件
            ((cls.model.run.is_null(True)) |                          # 运行状态为 NULL
             (cls.model.run != TaskStatus.CANCEL.value)),             # 或未取消
            (((cls.model.progress < 1) & (cls.model.progress > 0)) |  # 进度在 0~1 之间
             (cls.model.id.in_(unfinished_task_query)) |             # 或有未完成任务
             ((cls.model.progress == -1) &                            # 或进度为 -1（失败）
              (cls.model.run == TaskStatus.FAIL.value) &              # 运行状态为 FAIL
              (cls.model.id.in_(docs_with_non_failed_tasks))))        # 且有非失败任务（可重试）
        )

        return list(docs.dicts())

    @classmethod
    @DB.connection_context()
    def increment_chunk_num(cls, doc_id, kb_id, token_num, chunk_num, duration):
        """
        递增文档和知识库的 token_num、chunk_num，累加处理时长

        逻辑说明：
        1. 更新文档的 token_num、chunk_num、process_duration
        2. 如果文档不存在，记录警告
        3. 更新知识库的 token_num、chunk_num

        :param doc_id: 文档 ID
        :param kb_id: 知识库 ID
        :param token_num: 要增加的 token 数量
        :param chunk_num: 要增加的 chunk 数量
        :param duration: 要累加的处理时长
        :return: 受影响的行数
        """
        # 更新文档的计数和处理时长
        num = (
            cls.model.update(
                token_num=cls.model.token_num + token_num,
                chunk_num=cls.model.chunk_num + chunk_num,
                process_duration=cls.model.process_duration + duration
            )
            .where(cls.model.id == doc_id)
            .execute()
        )

        # 如果文档不存在，记录警告
        if num == 0:
            logging.warning("Document not found which is supposed to be there")

        # 更新知识库的计数
        num = Knowledgebase.update(
            token_num=Knowledgebase.token_num + token_num,
            chunk_num=Knowledgebase.chunk_num + chunk_num
        ).where(Knowledgebase.id == kb_id).execute()

        return num

    @classmethod
    @DB.connection_context()
    def decrement_chunk_num(cls, doc_id, kb_id, token_num, chunk_num, duration):
        """
        递减文档和知识库的 token_num、chunk_num（重新解析时使用），累加处理时长

        逻辑说明：
        1. 更新文档的 token_num、chunk_num、process_duration
        2. 如果文档不存在，抛出异常
        3. 更新知识库的 token_num、chunk_num

        :param doc_id: 文档 ID
        :param kb_id: 知识库 ID
        :param token_num: 要减少的 token 数量
        :param chunk_num: 要减少的 chunk 数量
        :param duration: 要累加的处理时长
        :return: 受影响的行数
        :raises LookupError: 文档不存在时
        """
        # 更新文档的计数和处理时长
        num = (
            cls.model.update(
                token_num=cls.model.token_num - token_num,
                chunk_num=cls.model.chunk_num - chunk_num,
                process_duration=cls.model.process_duration + duration
            )
            .where(cls.model.id == doc_id)
            .execute()
        )

        # 如果文档不存在，抛出异常
        if num == 0:
            raise LookupError("Document not found which is supposed to be there")

        # 更新知识库的计数
        num = Knowledgebase.update(
            token_num=Knowledgebase.token_num - token_num,
            chunk_num=Knowledgebase.chunk_num - chunk_num
        ).where(Knowledgebase.id == kb_id).execute()

        return num

    @classmethod
    @retry_deadlock_operation()
    @DB.connection_context()
    def delete_document_and_update_kb_counts(cls, doc_id) -> bool:
        """
        原子性地删除文档记录并更新知识库计数器

        使用事务和行锁确保并发安全。

        逻辑说明：
        1. 开启事务
        2. 使用 FOR_UPDATE 锁定文档行
        3. 如果文档不存在，返回 False（幂等）
        4. 删除文档记录
        5. 如果删除失败，返回 False
        6. 更新知识库的计数器（token_num、chunk_num、doc_num）
        7. 提交事务并返回 True

        :param doc_id: 文档 ID
        :return: 如果文档被此调用删除则返回 True，如果已被并发请求删除则返回 False（幂等）
        """
        with DB.atomic():
            # 使用 FOR_UPDATE 锁定文档行（防止并发修改）
            doc = (
                cls.model.select(
                    cls.model.id,
                    cls.model.kb_id,
                    cls.model.token_num,
                    cls.model.chunk_num,
                )
                .where(cls.model.id == doc_id)
                .for_update()  # 行锁
                .get_or_none()
            )

            # 如果文档不存在，返回 False（幂等）
            if doc is None:
                return False

            # 删除文档记录
            deleted = cls.model.delete().where(cls.model.id == doc_id).execute()

            # 如果删除失败，返回 False
            if not deleted:
                return False

            # 更新知识库的计数器
            Knowledgebase.update(
                token_num=Knowledgebase.token_num - doc.token_num,
                chunk_num=Knowledgebase.chunk_num - doc.chunk_num,
                doc_num=Knowledgebase.doc_num - 1,
            ).where(Knowledgebase.id == doc.kb_id).execute()

        return True

    @classmethod
    @DB.connection_context()
    def clear_chunk_num(cls, doc_id):
        """
        清除文档的 chunk 数量（已弃用）

        Deprecated: 使用 delete_document_and_update_kb_counts 代替

        :param doc_id: 文档 ID
        :return: 受影响的行数
        """
        doc = cls.model.get_by_id(doc_id)
        assert doc, "Can't fine document in database."

        num = (
            Knowledgebase.update(
                token_num=Knowledgebase.token_num - doc.token_num,
                chunk_num=Knowledgebase.chunk_num - doc.chunk_num,
                doc_num=Knowledgebase.doc_num - 1
            )
            .where(Knowledgebase.id == doc.kb_id)
            .execute()
        )

        return num

    @classmethod
    @DB.connection_context()
    def clear_chunk_num_when_rerun(cls, doc_id):
        """
        重新运行文档解析时，将文档的 token/chunk 计数从知识库中扣减

        逻辑说明：
        1. 获取文档对象
        2. 从知识库的 token_num 和 chunk_num 中扣减文档的计数
        3. 不扣减 doc_num（因为文档仍然存在）

        :param doc_id: 文档 ID
        :return: 受影响的行数
        """
        doc = cls.model.get_by_id(doc_id)
        assert doc, "Can't fine document in database."

        num = (
            Knowledgebase.update(
                token_num=Knowledgebase.token_num - doc.token_num,
                chunk_num=Knowledgebase.chunk_num - doc.chunk_num,
            )
            .where(Knowledgebase.id == doc.kb_id)
            .execute()
        )

        return num

    @classmethod
    @DB.connection_context()
    def get_tenant_id(cls, doc_id):
        """
        通过文档 ID 查询所属租户 ID（通过 Knowledgebase 关联）

        逻辑说明：
        1. 关联知识库表
        2. 按文档 ID 过滤
        3. 只选择有效状态的知识库
        4. 返回租户 ID

        :param doc_id: 文档 ID
        :return: 租户 ID，如果未找到则返回 None
        """
        docs = (
            cls.model.select(Knowledgebase.tenant_id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.id == doc_id, Knowledgebase.status == StatusEnum.VALID.value)
        )
        docs = docs.dicts()

        if not docs:
            return None

        return docs[0]["tenant_id"]

    @classmethod
    @DB.connection_context()
    def get_knowledgebase_id(cls, doc_id):
        """
        通过文档 ID 获取其所属知识库 ID

        逻辑说明：
        1. 按文档 ID 查询
        2. 只选择 kb_id 字段
        3. 返回知识库 ID

        :param doc_id: 文档 ID
        :return: 知识库 ID，如果未找到则返回 None
        """
        docs = cls.model.select(cls.model.kb_id).where(cls.model.id == doc_id)
        docs = docs.dicts()

        if not docs:
            return None

        return docs[0]["kb_id"]

    @classmethod
    @DB.connection_context()
    def get_tenant_id_by_name(cls, name):
        """
        通过文档名称查询所属租户 ID

        逻辑说明：
        1. 关联知识库表
        2. 按文档名称过滤
        3. 只选择有效状态的知识库
        4. 返回租户 ID

        :param name: 文档名称
        :return: 租户 ID，如果未找到则返回 None
        """
        docs = (
            cls.model.select(Knowledgebase.tenant_id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.name == name, Knowledgebase.status == StatusEnum.VALID.value)
        )
        docs = docs.dicts()

        if not docs:
            return None

        return docs[0]["tenant_id"]

    @classmethod
    @DB.connection_context()
    def accessible(cls, doc_id, user_id):
        """
        检查用户是否有权访问该文档

        用户需属于文档所在知识库的租户。

        逻辑说明：
        1. 关联知识库和用户租户关系表
        2. 按文档 ID 和用户 ID 过滤
        3. 如果有结果，返回 True；否则返回 False

        :param doc_id: 文档 ID
        :param user_id: 用户 ID
        :return: 是否有访问权限
        """
        docs = (
            cls.model.select(cls.model.id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .join(UserTenant, on=(UserTenant.tenant_id == Knowledgebase.tenant_id))
            .where(cls.model.id == doc_id, UserTenant.user_id == user_id)
            .paginate(0, 1)
        )
        docs = docs.dicts()

        if not docs:
            return False

        return True

    @classmethod
    @DB.connection_context()
    def accessible4deletion(cls, doc_id, user_id):
        """
        检查用户是否有权删除该文档

        需为知识库创建者的租户中的 NORMAL 或 OWNER 角色。

        逻辑说明：
        1. 关联知识库和用户租户关系表
        2. 关联条件：知识库的创建者 = 用户租户关系的租户
        3. 按文档 ID、用户 ID、角色过滤
        4. 如果有结果，返回 True；否则返回 False

        :param doc_id: 文档 ID
        :param user_id: 用户 ID
        :return: 是否有删除权限
        """
        docs = (
            cls.model.select(cls.model.id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .join(UserTenant, on=((UserTenant.tenant_id == Knowledgebase.created_by) & (UserTenant.user_id == user_id)))
            .where(
                cls.model.id == doc_id,
                UserTenant.status == StatusEnum.VALID.value,
                ((UserTenant.role == UserTenantRole.NORMAL) | (UserTenant.role == UserTenantRole.OWNER))
            )
            .paginate(0, 1)
        )
        docs = docs.dicts()

        if not docs:
            return False

        return True

    @classmethod
    @DB.connection_context()
    def get_embd_id(cls, doc_id):
        """
        获取文档所属知识库配置的 embedding 模型 ID

        逻辑说明：
        1. 关联知识库表
        2. 选择 embd_id 字段
        3. 按文档 ID 过滤
        4. 只选择有效状态的知识库
        5. 返回 embedding 模型 ID

        :param doc_id: 文档 ID
        :return: embedding 模型 ID，如果未找到则返回 None
        """
        docs = (
            cls.model.select(Knowledgebase.embd_id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.id == doc_id, Knowledgebase.status == StatusEnum.VALID.value)
        )
        docs = docs.dicts()

        if not docs:
            return None

        return docs[0]["embd_id"]

    @classmethod
    @DB.connection_context()
    def get_tenant_embd_id(cls, doc_id):
        """
        获取文档所属知识库的租户级 embedding 模型 ID

        逻辑说明：
        1. 关联知识库表
        2. 选择 tenant_embd_id 字段
        3. 按文档 ID 过滤
        4. 只选择有效状态的知识库
        5. 返回租户级 embedding 模型 ID

        :param doc_id: 文档 ID
        :return: 租户级 embedding 模型 ID，如果未找到则返回 None
        """
        docs = (
            cls.model.select(Knowledgebase.tenant_embd_id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.id == doc_id, Knowledgebase.status == StatusEnum.VALID.value)
        )
        docs = docs.dicts()

        if not docs:
            return None

        return docs[0]["tenant_embd_id"]

    @classmethod
    @DB.connection_context()
    def get_chunking_config(cls, doc_id):
        """
        获取文档的完整分块配置

        包括解析器 ID、parser_config、知识库语言、
        embedding 模型、以及租户的 img2txt/asr/llm 模型信息。

        逻辑说明：
        1. 关联知识库和租户表
        2. 选择所有相关配置字段
        3. 按文档 ID 过滤
        4. 返回配置字典

        :param doc_id: 文档 ID
        :return: 配置字典，如果未找到则返回 None
        """
        configs = (
            cls.model.select(
                cls.model.id,
                cls.model.kb_id,
                cls.model.parser_id,
                cls.model.parser_config,
                cls.model.size,
                cls.model.content_hash,
                Knowledgebase.language,
                Knowledgebase.embd_id,
                Tenant.id.alias("tenant_id"),
                Tenant.img2txt_id,
                Tenant.asr_id,
                Tenant.llm_id,
            )
            .join(Knowledgebase, on=(cls.model.kb_id == Knowledgebase.id))
            .join(Tenant, on=(Knowledgebase.tenant_id == Tenant.id))
            .where(cls.model.id == doc_id)
        )
        configs = configs.dicts()

        if not configs:
            return None

        return configs[0]

    @classmethod
    @DB.connection_context()
    def get_doc_id_by_doc_name(cls, doc_name):
        """
        通过文档名称获取文档 ID

        逻辑说明：
        1. 按文档名称精确匹配
        2. 只返回 id 字段
        3. 返回文档 ID

        :param doc_name: 文档名称
        :return: 文档 ID，如果未找到则返回 None
        """
        fields = [cls.model.id]
        doc_id = cls.model.select(*fields).where(cls.model.name == doc_name)
        doc_id = doc_id.dicts()

        if not doc_id:
            return None

        return doc_id[0]["id"]

    @classmethod
    @DB.connection_context()
    def get_doc_ids_by_doc_names(cls, doc_names):
        """
        通过文档名称列表获取文档 ID 列表

        逻辑说明：
        1. 如果文档名称列表为空，返回空列表
        2. 使用 IN 子句匹配多个文档名称
        3. 返回文档 ID 列表

        :param doc_names: 文档名称列表
        :return: 文档 ID 列表
        """
        if not doc_names:
            return []

        query = cls.model.select(cls.model.id).where(cls.model.name.in_(doc_names))
        return list(query.scalars().iterator())

    @classmethod
    @DB.connection_context()
    def get_thumbnails(cls, docids):
        """
        批量获取文档的缩略图信息

        逻辑说明：
        1. 选择 id、kb_id、thumbnail 字段
        2. 使用 IN 子句匹配多个文档 ID
        3. 返回文档列表

        :param docids: 文档 ID 列表
        :return: 文档列表（包含缩略图信息）
        """
        fields = [cls.model.id, cls.model.kb_id, cls.model.thumbnail]
        return list(cls.model.select(*fields).where(cls.model.id.in_(docids)).dicts())

    @classmethod
    @DB.connection_context()
    def update_parser_config(cls, id, config):
        """
        深度合并更新文档的解析器配置

        使用 DFS 递归合并：新增字段直接添加，字典类型递归合并，其他类型覆盖。
        如果新配置不含 "raptor" 但旧配置有，则移除旧配置中的 "raptor"。

        逻辑说明：
        1. 如果配置为空，直接返回
        2. 获取文档对象
        3. 使用 DFS 递归合并配置
        4. 如果新配置没有 raptor 但旧配置有，删除 raptor
        5. 更新文档配置

        :param id: 文档 ID
        :param config: 新的解析器配置
        :raises LookupError: 文档不存在时
        """
        if not config:
            return

        # 获取文档对象
        e, d = cls.get_by_id(id)
        if not e:
            raise LookupError(f"Document({id}) not found.")

        def dfs_update(old, new):
            """
            深度优先递归合并字典

            逻辑说明：
            1. 遍历新字典的所有键值对
            2. 如果旧字典没有该键，直接添加
            3. 如果新旧值都是字典，递归合并
            4. 否则，用新值覆盖旧值
            """
            for k, v in new.items():
                if k not in old:
                    # 新增的键，直接添加
                    old[k] = v
                    continue

                if isinstance(v, dict) and isinstance(old[k], dict):
                    # 都是字典，递归合并
                    dfs_update(old[k], v)
                else:
                    # 其他类型，直接覆盖
                    old[k] = v

        # 执行深度合并
        dfs_update(d.parser_config, config)

        # 如果新配置没有 raptor 但旧配置有，删除 raptor
        if not config.get("raptor") and d.parser_config.get("raptor"):
            del d.parser_config["raptor"]

        # 更新文档配置
        cls.update_by_id(id, {"parser_config": d.parser_config})

    @classmethod
    @DB.connection_context()
    def get_doc_count(cls, tenant_id):
        """
        获取租户的文档总数

        逻辑说明：
        1. 关联知识库表
        2. 按租户 ID 过滤
        3. 返回文档数量

        :param tenant_id: 租户 ID
        :return: 文档数量
        """
        docs = (
            cls.model.select(cls.model.id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(Knowledgebase.tenant_id == tenant_id)
        )
        return len(docs)

    @classmethod
    @DB.connection_context()
    def begin2parse(cls, doc_id, keep_progress=False):
        """
        标记文档开始解析，设置进度消息和处理开始时间

        keep_progress=False 时重置进度和运行状态（普通解析任务）；
        keep_progress=True 时保留当前进度（用于 GraphRAG/RAPTOR/Mindmap 等后处理任务）。

        逻辑说明：
        1. 构建更新信息字典
        2. 设置进度消息为"Task is queued..."
        3. 设置处理开始时间
        4. 如果 keep_progress=False，重置进度和运行状态
        5. 如果 keep_progress=True，保留当前进度（用于后处理任务）

        :param doc_id: 文档 ID
        :param keep_progress: 是否保留当前进度
        """
        info = {
            "progress_msg": "Task is queued...",
            "process_begin_at": get_format_time(),
        }

        if not keep_progress:
            # 普通解析任务：重置进度和运行状态
            info["progress"] = random.random() * 1 / 100.0  # 设置为一个小的正数
            info["run"] = TaskStatus.RUNNING.value

        # 更新文档信息
        cls.update_by_id(doc_id, info)

    @classmethod
    @DB.connection_context()
    def update_progress(cls):
        """
        定时同步所有未完成文档的解析进度

        由后台调度器定期调用。

        逻辑说明：
        1. 获取所有未完成的文档
        2. 调用 _sync_progress 同步进度
        """
        docs = cls.get_unfinished_docs()
        cls._sync_progress(docs)

    @classmethod
    @DB.connection_context()
    def update_progress_immediately(cls, docs: list[dict]):
        """
        立即同步指定文档列表的解析进度

        无需查询未完成文档，直接同步给定的文档列表。

        逻辑说明：
        1. 如果文档列表为空，直接返回
        2. 调用 _sync_progress 同步进度

        :param docs: 文档列表
        """
        if not docs:
            return

        cls._sync_progress(docs)

    @classmethod
    @DB.connection_context()
    def _sync_progress(cls, docs: list[dict]):
        """
        核心进度同步逻辑：遍历每个文档，聚合其所有子任务的进度和状态

        对于每个文档：
        1. 查询该文档的所有任务
        2. 聚合进度（取平均）和状态消息
        3. 根据任务完成情况判定文档状态：全部完成→DONE，有失败→FAIL，否则→RUNNING
        4. 对于特殊任务类型（GraphRAG/RAPTOR/Mindmap）且文档已解析完成时，冻结进度不回退
        5. 更新文档的进度、状态、处理时长等信息到数据库

        逻辑说明：
        1. 遍历每个文档
        2. 查询该文档的所有任务
        3. 聚合任务的进度和状态消息
        4. 计算平均进度
        5. 根据任务完成情况确定文档状态
        6. 检查是否需要冻结进度（特殊任务运行中）
        7. 更新文档信息到数据库

        :param docs: 文档列表
        """
        from api.db.services.task_service import TaskService

        for d in docs:
            try:
                # 查询文档的所有任务（按创建时间排序）
                tsks = TaskService.query(doc_id=d["id"], order_by=Task.create_time)

                if not tsks:
                    continue

                # 初始化统计变量
                msg = []  # 状态消息列表
                prg = 0    # 总进度
                finished = True  # 是否全部完成
                bad = 0  # 失败任务计数

                # 获取文档当前状态
                e, doc = DocumentService.get_by_id(d["id"])
                status = doc.run

                # 如果文档已取消，跳过
                if status == TaskStatus.CANCEL.value:
                    continue

                doc_progress = doc.progress if doc and doc.progress else 0.0
                special_task_running = False  # 特殊任务是否运行中
                priority = 0  # 最高优先级

                # 遍历所有任务，聚合进度和状态
                for t in tsks:
                    task_type = (t.task_type or "").lower()

                    # 检查是否为特殊任务类型
                    if task_type in PIPELINE_SPECIAL_PROGRESS_FREEZE_TASK_TYPES:
                        special_task_running = True

                    # 统计任务完成情况
                    if 0 <= t.progress < 1:
                        finished = False
                    if t.progress == -1:
                        bad += 1

                    # 累加进度（跳过负进度）
                    prg += t.progress if t.progress >= 0 else 0

                    # 收集状态消息
                    if t.progress_msg.strip():
                        msg.append(t.progress_msg)

                    # 记录最高优先级
                    priority = max(priority, t.priority)

                # 计算平均进度
                prg /= len(tsks)

                # 根据任务完成情况确定文档状态
                if finished and bad:
                    # 所有任务结束但有失败的 → 标记为 FAIL
                    prg = -1
                    status = TaskStatus.FAIL.value
                elif finished:
                    # 所有任务正常结束 → 标记为 DONE
                    prg = 1
                    status = TaskStatus.DONE.value
                elif not finished:
                    status = TaskStatus.RUNNING.value

                # 特殊任务冻结条件：特殊任务运行中 + 文档已解析完成 + 整体未完成
                # 防止后处理任务（如 GraphRAG）导致已完成的主解析进度被覆盖
                freeze_progress = special_task_running and doc_progress >= 1 and not finished

                # 合并状态消息并排序
                msg = "\n".join(sorted(msg))

                # 获取处理开始时间
                begin_at = d.get("process_begin_at")
                if not begin_at:
                    # 兜底：如果数据库中没有开始时间，使用当前时间并回写
                    begin_at = datetime.now()
                    cls.update_by_id(d["id"], {"process_begin_at": begin_at})

                # 构建更新信息
                info = {
                    "process_duration": max(datetime.timestamp(datetime.now()) - begin_at.timestamp(), 0),
                    "run": status
                }

                # 如果进度不为 0 且不冻结，更新进度
                if prg != 0 and not freeze_progress:
                    info["progress"] = prg

                # 更新进度消息
                if msg:
                    info["progress_msg"] = msg
                    # 特殊任务创建时，追加队列等待信息
                    if msg.endswith("created task graphrag") or msg.endswith("created task raptor") or msg.endswith("created task mindmap"):
                        info["progress_msg"] += "\n%d tasks are ahead in the queue..." % get_queue_length(priority)
                else:
                    # 没有消息时，显示队列等待信息
                    info["progress_msg"] = "%d tasks are ahead in the queue..." % get_queue_length(priority)

                # 更新时间戳
                info["update_time"] = current_timestamp()
                info["update_date"] = get_format_time()

                # 仅更新未取消的文档，跳过已被用户取消的文档
                (
                    cls.model.update(info)
                    .where(
                        (cls.model.id == d["id"]) &
                        ((cls.model.run.is_null(True)) | (cls.model.run != TaskStatus.CANCEL.value))
                    )
                    .execute()
                )

            except Exception as e:
                # 忽略特定的错误（'0' 错误）
                if str(e).find("'0'") < 0:
                    logging.exception("fetch task exception")

    @classmethod
    @DB.connection_context()
    def get_kb_doc_count(cls, kb_id):
        """
        获取知识库的文档数量

        逻辑说明：
        1. 按知识库 ID 过滤
        2. 返回文档数量

        :param kb_id: 知识库 ID
        :return: 文档数量
        """
        return cls.model.select().where(cls.model.kb_id == kb_id).count()

    @classmethod
    @DB.connection_context()
    def get_all_kb_doc_count(cls):
        """
        获取所有知识库的文档数量统计

        逻辑说明：
        1. 按知识库 ID 分组
        2. 统计每个知识库的文档数量
        3. 返回字典 {kb_id: count}

        :return: 字典，键为知识库 ID，值为文档数量
        """
        result = {}
        rows = cls.model.select(cls.model.kb_id, fn.COUNT(cls.model.id).alias("count")).group_by(cls.model.kb_id)

        for row in rows:
            result[row.kb_id] = row.count

        return result

    @classmethod
    @DB.connection_context()
    def do_cancel(cls, doc_id):
        """
        检查文档是否已被取消或解析失败

        逻辑说明：
        1. 获取文档对象
        2. 检查运行状态是否为 CANCEL
        3. 检查进度是否为负数（表示失败）
        4. 返回是否应该取消

        :param doc_id: 文档 ID
        :return: 是否已取消或失败
        """
        try:
            _, doc = DocumentService.get_by_id(doc_id)
            return doc.run == TaskStatus.CANCEL.value or doc.progress < 0
        except Exception:
            pass

        return False

    @classmethod
    @DB.connection_context()
    def knowledgebase_basic_info(cls, kb_id: str) -> dict[str, int]:
        """
        获取知识库下文档的聚合统计信息

        返回：处理中、已完成、失败、已取消、已下载数量

        逻辑说明：
        1. 统计已取消的文档数（run == "2"）
        2. 统计已下载的文档数（source_type != "local"）
        3. 使用 CASE WHEN 统计处理中、已完成、失败的文档数
        4. 返回统计字典

        :param kb_id: 知识库 ID
        :return: 统计字典，包含 processing、finished、failed、cancelled、downloaded
        """
        # 统计已取消的文档（run == "2"）
        cancelled = cls.model.select(fn.COUNT(1)).where(
            (cls.model.kb_id == kb_id) & (cls.model.run == TaskStatus.CANCEL)
        ).scalar()

        # 统计已下载的文档（source_type != "local"）
        downloaded = cls.model.select(fn.COUNT(1)).where(
            cls.model.kb_id == kb_id, cls.model.source_type != "local"
        ).scalar()

        # 统计处理中、已完成、失败的文档数
        row = (
            cls.model.select(
                # finished: progress == 1
                fn.COALESCE(fn.SUM(Case(None, [(cls.model.progress == 1, 1)], 0)), 0).alias("finished"),
                # failed: progress == -1
                fn.COALESCE(fn.SUM(Case(None, [(cls.model.progress == -1, 1)], 0)), 0).alias("failed"),
                # processing: 0 <= progress < 1
                fn.COALESCE(
                    fn.SUM(
                        Case(
                            None,
                            [
                                (((cls.model.progress == 0) | ((cls.model.progress > 0) & (cls.model.progress < 1))), 1),
                            ],
                            0,
                        )
                    ),
                    0,
                ).alias("processing"),
            )
            .where((cls.model.kb_id == kb_id) & ((cls.model.run.is_null(True)) | (cls.model.run != TaskStatus.CANCEL)))
            .dicts()
            .get()
        )

        return {
            "processing": int(row["processing"]),
            "finished": int(row["finished"]),
            "failed": int(row["failed"]),
            "cancelled": int(cancelled),
            "downloaded": int(downloaded)
        }

    @classmethod
    def run(cls, tenant_id: str, doc: dict, kb_table_num_map: dict):
        """
        启动文档解析任务

        如果文档配置了 pipeline_id，则走 DataFlow 流程；
        否则根据文件存储地址创建普通解析任务。
        对于 TABLE 类型解析器，会检查并清理知识库的 field_map。

        逻辑说明：
        1. 添加租户 ID 到文档信息
        2. 检查是否为 TABLE 类型解析器
        3. 如果是 TABLE 类型且知识库为空，清理 field_map
        4. 如果配置了 pipeline_id，创建 DataFlow 任务
        5. 否则，创建普通解析任务

        :param tenant_id: 租户 ID
        :param doc: 文档字典
        :param kb_table_num_map: 知识库表格数量映射（用于判断是否清理 field_map）
        """
        from api.db.services.task_service import queue_dataflow, queue_tasks
        from api.db.services.file2document_service import File2DocumentService

        doc["tenant_id"] = tenant_id
        doc_parser = doc.get("parser_id", ParserType.NAIVE)

        # 处理 TABLE 类型解析器
        if doc_parser == ParserType.TABLE:
            kb_id = doc.get("kb_id")
            if not kb_id:
                return

            # 如果知识库不在映射表中，查询并缓存
            if kb_id not in kb_table_num_map:
                count = DocumentService.count_by_kb_id(kb_id=kb_id, keywords="", run_status=[TaskStatus.DONE], types=[])
                kb_table_num_map[kb_id] = count

                # 如果知识库为空，清理 field_map
                if kb_table_num_map[kb_id] <= 0:
                    KnowledgebaseService.delete_field_map(kb_id)

        # 根据配置创建不同类型的任务
        if doc.get("pipeline_id", ""):
            # 配置了 pipeline_id，创建 DataFlow 任务
            queue_dataflow(tenant_id, flow_id=doc["pipeline_id"], task_id=get_uuid(), doc_id=doc["id"])
        else:
            # 普通解析任务
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc["id"])
            queue_tasks(doc, bucket, name, 0)


def queue_raptor_o_graphrag_tasks(sample_doc_id, ty, priority, fake_doc_id="", doc_ids=[]):
    """
    创建 GraphRAG / RAPTOR / Mindmap 后处理任务并推入 Redis 队列

    使用 xxhash 对分块配置和任务参数生成摘要（digest），用于任务去重。
    通过 fake_doc_id 可绕过知识库级别的任务限制；
    通过 doc_ids 指定参与任务的文档范围。

    逻辑说明：
    1. 验证任务类型（graphrag、raptor 或 mindmap）
    2. 获取文档的分块配置
    3. 使用 xxhash 生成任务摘要（用于去重）
    4. 创建新任务
    5. 插入任务到数据库
    6. 设置 fake_doc_id 和 doc_ids
    7. 标记文档开始解析（保持进度）
    8. 将任务推入 Redis 队列

    :param sample_doc_id: 示例文档 ID（用于获取配置）
    :param ty: 任务类型（"graphrag"、"raptor" 或 "mindmap"）
    :param priority: 任务优先级
    :param fake_doc_id: 虚拟文档 ID（用于绕过知识库级别限制）
    :param doc_ids: 参与任务的文档 ID 列表
    :return: 任务 ID
    :raises AssertionError: 任务类型不正确时
    """
    assert ty in ["graphrag", "raptor", "mindmap"], "type should be graphrag, raptor or mindmap"

    # 获取分块配置
    chunking_config = DocumentService.get_chunking_config(sample_doc_id["id"])

    # 使用 xxhash 生成任务摘要
    hasher = xxhash.xxh64()
    for field in sorted(chunking_config.keys()):
        hasher.update(str(chunking_config[field]).encode("utf-8"))

    def new_task():
        """构建一个新的后处理任务字典模板"""
        return {
            "id": get_uuid(),
            "doc_id": sample_doc_id["id"],
            "from_page": 100000000,
            "to_page": 100000000,
            "task_type": ty,
            "progress_msg": datetime.now().strftime("%H:%M:%S") + " created task " + ty,
            "begin_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # 创建任务并生成摘要
    task = new_task()
    for field in ["doc_id", "from_page", "to_page"]:
        hasher.update(str(task.get(field, "")).encode("utf-8"))
    hasher.update(ty.encode("utf-8"))
    task["digest"] = hasher.hexdigest()  # 基于配置+参数的摘要，用于去重

    # 插入任务到数据库
    bulk_insert_into_db(Task, [task], True)

    # 设置 fake_doc_id 和 doc_ids
    task["doc_id"] = fake_doc_id  # 用 fake_doc_id 替换实际 doc_id（绕过 KB 级别限制）
    task["doc_ids"] = doc_ids  # 记录实际参与任务的文档 ID 列表

    # 标记文档开始解析（保持进度不重置）
    DocumentService.begin2parse(sample_doc_id["id"], keep_progress=True)

    # 将任务推入 Redis 队列
    assert REDIS_CONN.queue_product(settings.get_svr_queue_name(priority), message=task), "Can't access Redis. Please check the Redis' status."

    return task["id"]


def get_queue_length(priority):
    """
    查询指定优先级的 Redis 队列中待处理消息数量（lag）

    逻辑说明：
        1. 获取队列信息
        2. 从队列信息中提取 lag 值
        3. 返回 lag 值（待处理消息数量）

    :param priority: 队列优先级
    :return: 待处理消息数量
    """
    group_info = REDIS_CONN.queue_info(settings.get_svr_queue_name(priority), SVR_CONSUMER_GROUP_NAME)
    if not group_info:
        return 0
    return int(group_info.get("lag", 0) or 0)


def doc_upload_and_parse(conversation_id, file_objs, user_id):
    """
    在对话上下文中上传文件并同步解析

    流程：
    1. 通过会话 ID 找到关联的知识库和 embedding 模型
    2. 上传文件并创建文档记录
    3. 使用线程池并发执行文件分块（支持多种解析器）
    4. 对非图片文档生成思维导图（MindMap）
    5. 对所有 chunks 进行 embedding 并批量写入 ES/Infinity
    6. 更新文档的 chunk/token 计数

    逻辑说明：
    1. 查找会话（支持普通会话和 API 会话）
    2. 获取对话关联的第一个知识库
    3. 获取 embedding 模型配置
    4. 上传文件到知识库
    5. 根据文档类型选择解析器
    6. 使用线程池并发执行文件分块
    7. 处理分块结果（含图片的 chunk 存储到对象存储）
    8. 对非图片文档生成思维导图
    9. 对所有 chunks 进行 embedding
    10. 批量写入文档存储
    11. 更新文档的计数

    :param conversation_id: 会话 ID
    :param file_objs: 文件对象列表
    :param user_id: 用户 ID
    :return: 所有新创建的文档 ID 列表
    """
    from api.db.services.api_service import API4ConversationService
    from api.db.services.conversation_service import ConversationService
    from api.db.services.dialog_service import DialogService
    from api.db.services.file_service import FileService
    from api.db.services.llm_service import LLMBundle
    from api.db.services.user_service import TenantService
    from api.db.joint_services.tenant_model_service import get_model_config_by_id, get_model_config_by_type_and_name, get_tenant_default_model_by_type
    from rag.app import audio, email, naive, picture, presentation

    # 查找会话（支持普通会话和 API 会话）
    e, conv = ConversationService.get_by_id(conversation_id)
    if not e:
        e, conv = API4ConversationService.get_by_id(conversation_id)
    assert e, "Conversation not found!"

    # 获取对话关联的第一个知识库
    e, dia = DialogService.get_by_id(conv.dialog_id)
    if not dia.kb_ids:
        raise LookupError("No dataset associated with this conversation. Please add a dataset before uploading documents")
    kb_id = dia.kb_ids[0]

    # 获取知识库信息
    e, kb = KnowledgebaseService.get_by_id(kb_id)
    if not e:
        raise LookupError("Can't find this dataset!")

    # 获取 embedding 模型配置
    if kb.tenant_embd_id:
        embd_model_config = get_model_config_by_id(kb.tenant_embd_id)
    else:
        embd_model_config = get_model_config_by_type_and_name(kb.tenant_id, LLMType.EMBEDDING, kb.embd_id)
    embd_mdl = LLMBundle(kb.tenant_id, embd_model_config, lang=kb.language)

    # 上传文件到知识库
    err, files = FileService.upload_document(kb, file_objs, user_id)
    assert not err, "\n".join(err)

    def dummy(prog=None, msg=""):
        """空回调函数"""
        pass

    # 解析器工厂映射：根据文档类型选择对应的解析模块
    FACTORY = {
        ParserType.PRESENTATION.value: presentation,
        ParserType.PICTURE.value: picture,
        ParserType.AUDIO.value: audio,
        ParserType.EMAIL.value: email
    }

    # 默认解析器配置
    parser_config = {
        "chunk_token_num": 4096,
        "delimiter": "\n!?;。；！？",
        "layout_recognize": "Plain Text",
        "table_context_size": 0,
        "image_context_size": 0
    }

    # 创建线程池（最大 12 个工作线程）
    exe = ThreadPoolExecutor(max_workers=12)
    threads = []

    # 收集文档名称
    doc_nm = {}
    for d, blob in files:
        doc_nm[d["id"]] = d["name"]

    # 使用线程池并发执行文件分块
    for d, blob in files:
        kwargs = {
            "callback": dummy,
            "parser_config": parser_config,
            "from_page": 0,
            "to_page": 100000,
            "tenant_id": kb.tenant_id,
            "lang": kb.language
        }
        threads.append(exe.submit(FACTORY.get(d["parser_id"], naive).chunk, d["name"], blob, **kwargs))

    # 收集分块结果
    docs = []
    for (docinfo, _), th in zip(files, threads):
        # 处理含图片的 chunk：将图片存储到对象存储并替换为 img_id 引用
        doc = {"doc_id": docinfo["id"], "kb_id": [kb.id]}

        for ck in th.result():
            d = deepcopy(doc)
            d.update(ck)

            # 生成 chunk ID（使用 xxhash）
            d["id"] = xxhash.xxh64((ck["content_with_weight"] + str(d["doc_id"])).encode("utf-8")).hexdigest()

            # 设置时间戳
            d["create_time"] = str(datetime.now()).replace("T", " ")[:19]
            d["create_timestamp_flt"] = datetime.now().timestamp()

            # 处理不包含图片的 chunk
            if not d.get("image"):
                docs.append(d)
                continue

            # 处理包含图片的 chunk
            output_buffer = BytesIO()
            if isinstance(d["image"], bytes):
                output_buffer = BytesIO(d["image"])
            else:
                d["image"].save(output_buffer, format="JPEG")

            # 存储图片到对象存储
            settings.STORAGE_IMPL.put(kb.id, d["id"], output_buffer.getvalue())
            d["img_id"] = "{}-{}".format(kb.id, d["id"])
            d.pop("image", None)
            docs.append(d)

    # 收集解析器类型和文档 ID
    parser_ids = {d["id"]: d["parser_id"] for d, _ in files}
    docids = [d["id"] for d, _ in files]

    # 初始化计数器
    chunk_counts = {id: 0 for id in docids}
    token_counts = {id: 0 for id in docids}
    es_bulk_size = 64  # ES 批量插入大小

    def embedding(doc_id, cnts, batch_size=16):
        """
        对一批文本内容进行 embedding，返回向量列表，同时统计 chunk 和 token 数量

        逻辑说明：
        1. 分批进行 embedding（每批 batch_size 个）
        2. 累加 chunk 数量和 token 数量
        3. 返回向量列表
        """
        nonlocal embd_mdl, chunk_counts, token_counts
        vectors = []

        for i in range(0, len(cnts), batch_size):
            # 对一批文本进行 encoding
            vts, c = embd_mdl.encode(cnts[i : i + batch_size])
            vectors.extend(vts.tolist())

            # 累加计数
            chunk_counts[doc_id] += len(cnts[i : i + batch_size])
            token_counts[doc_id] += c

        return vectors

    # 获取索引名称
    idxnm = search.index_name(kb.tenant_id)
    try_create_idx = True

    # 获取租户 LLM 配置（用于生成思维导图）
    _, tenant = TenantService.get_by_id(kb.tenant_id)
    tenant_llm_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
    llm_bdl = LLMBundle(kb.tenant_id, tenant_llm_config)

    # 处理每个文档
    for doc_id in docids:
        # 获取该文档的所有 chunks
        cks = [c for c in docs if c["doc_id"] == doc_id]

        # 对非图片文档生成思维导图（使用 LLM 抽取）
        if parser_ids[doc_id] != ParserType.PICTURE.value:
            from rag.graphrag.general.mind_map_extractor import MindMapExtractor

            mindmap = MindMapExtractor(llm_bdl)
            try:
                # 生成思维导图
                mind_map = asyncio.run(mindmap([c["content_with_weight"] for c in docs if c["doc_id"] == doc_id]))
                mind_map = json.dumps(mind_map.output, ensure_ascii=False, indent=2)

                # 检查思维导图内容是否过少
                if len(mind_map) < 32:
                    raise Exception("Few content: " + mind_map)

                # 添加思维导图作为一个特殊的 chunk
                cks.append(
                    {
                        "id": get_uuid(),
                        "doc_id": doc_id,
                        "kb_id": [kb.id],
                        "docnm_kwd": doc_nm[doc_id],
                        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", doc_nm[doc_id])),
                        "content_ltks": rag_tokenizer.tokenize("summary summarize 总结 概况 file 文件 概括"),
                        "content_with_weight": mind_map,
                        "knowledge_graph_kwd": "mind_map",
                    }
                )
            except Exception:
                logging.exception("Mind map generation error")

        # 对所有 chunks 进行 embedding
        vectors = embedding(doc_id, [c["content_with_weight"] for c in cks])
        assert len(cks) == len(vectors)

        # 将 embedding 向量附加到每个 chunk
        for i, d in enumerate(cks):
            v = vectors[i]
            d["q_%d_vec" % len(v)] = v

        # 批量写入文档存储（ES/Infinity）
        for b in range(0, len(cks), es_bulk_size):
            if try_create_idx:
                # 首次写入时创建索引
                if not settings.docStoreConn.index_exist(idxnm, kb_id):
                    settings.docStoreConn.create_idx(idxnm, kb_id, len(vectors[0]), kb.parser_id)
                try_create_idx = False

            # 批量插入 chunks
            settings.docStoreConn.insert(cks[b : b + es_bulk_size], idxnm, kb_id)

        # 更新文档的 chunk/token 计数
        DocumentService.increment_chunk_num(doc_id, kb.id, token_counts[doc_id], chunk_counts[doc_id], 0)

    return [d["id"] for d, _ in files]
