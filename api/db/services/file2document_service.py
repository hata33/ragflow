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
from datetime import datetime

from common.constants import FileSource
from api.db.db_models import DB
from api.db.db_models import File, File2Document
from api.db.services.common_service import CommonService
from api.db.services.document_service import DocumentService
from common.time_utils import current_timestamp, datetime_format


class File2DocumentService(CommonService):
    """文件与文档的关联服务层，管理 File2Document 中间表。

    File2Document 是 File（原始上传文件）和 Document（知识库文档）之间的映射表，
    用于追踪文件到文档的转换关系。支持本地文件和外部数据源文件两种来源。
    """

    model = File2Document

    @classmethod
    @DB.connection_context()
    def get_by_file_id(cls, file_id):
        """根据文件 ID 查询所有关联的 File2Document 记录。"""
        objs = cls.model.select().where(cls.model.file_id == file_id)
        return list(objs)

    @classmethod
    @DB.connection_context()
    def get_by_document_id(cls, document_id):
        """根据文档 ID 查询所有关联的 File2Document 记录。"""
        objs = cls.model.select().where(cls.model.document_id == document_id)
        return list(objs)

    @classmethod
    @DB.connection_context()
    def get_by_document_ids(cls, document_ids):
        """根据文档 ID 列表批量查询 File2Document 记录，返回字典列表。"""
        objs = cls.model.select().where(cls.model.document_id.in_(document_ids))
        return list(objs.dicts())

    @classmethod
    @DB.connection_context()
    def insert(cls, obj):
        """插入一条 File2Document 关联记录，失败时抛出运行时异常。"""
        if not cls.save(**obj):
            raise RuntimeError("Database error (File)!")
        return File2Document(**obj)

    @classmethod
    @DB.connection_context()
    def delete_by_file_id(cls, file_id):
        """根据文件 ID 删除所有关联的 File2Document 记录。"""
        return cls.model.delete().where(cls.model.file_id == file_id).execute()

    @classmethod
    @DB.connection_context()
    def delete_by_document_ids_or_file_ids(cls, document_ids, file_ids):
        """按文档 ID 或文件 ID 删除关联记录（OR 逻辑）。

        优先使用非空的那个列表作为条件；两者都非空时使用 OR 条件。
        """
        if not document_ids:
            return cls.model.delete().where(cls.model.file_id.in_(file_ids)).execute()
        elif not file_ids:
            return cls.model.delete().where(cls.model.document_id.in_(document_ids)).execute()
        return cls.model.delete().where(cls.model.document_id.in_(document_ids) | cls.model.file_id.in_(file_ids)).execute()

    @classmethod
    @DB.connection_context()
    def delete_by_document_id(cls, doc_id):
        """根据文档 ID 删除所有关联的 File2Document 记录。"""
        return cls.model.delete().where(cls.model.document_id == doc_id).execute()

    @classmethod
    @DB.connection_context()
    def update_by_file_id(cls, file_id, obj):
        """根据 file_id（实际匹配主键 id）更新记录，自动刷新更新时间戳。"""
        obj["update_time"] = current_timestamp()
        obj["update_date"] = datetime_format(datetime.now())
        cls.model.update(obj).where(cls.model.id == file_id).execute()
        return File2Document(**obj)

    @classmethod
    @DB.connection_context()
    def get_storage_address(cls, doc_id=None, file_id=None):
        """获取文件的实际存储地址（bucket, location）。

        查找逻辑：
        1. 通过 doc_id 或 file_id 查找 File2Document 关联记录
        2. 如果关联的 File 是本地文件（source_type == LOCAL），返回 File 的 parent_id 和 location
        3. 如果是外部数据源文件，则回退到 Document 表查找 kb_id 和 location
        4. 必须提供 doc_id 或 file_id 之一
        """
        if doc_id:
            f2d = cls.get_by_document_id(doc_id)
        else:
            f2d = cls.get_by_file_id(file_id)
        if f2d:
            file = File.get_by_id(f2d[0].file_id)
            if not file.source_type or file.source_type == FileSource.LOCAL:
                # 本地文件：bucket = file.parent_id（即知识库 ID），location = 文件路径
                return file.parent_id, file.location
            # 外部数据源文件：回退到 Document 表获取存储地址
            doc_id = f2d[0].document_id

        assert doc_id, "please specify doc_id"
        e, doc = DocumentService.get_by_id(doc_id)
        return doc.kb_id, doc.location
