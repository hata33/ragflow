#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""文档 API 服务模块

该模块提供了文档管理的核心功能，包括文档名称更新、分块方法更新、
状态更新、字段验证等辅助函数。
"""

from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.utils import validation_utils
from common import settings
from common.constants import TaskStatus
from api.utils.api_utils import get_error_data_result, server_error_response, get_parser_config
from api.utils.validation_utils import UpdateDocumentReq
from rag.nlp import rag_tokenizer, search


def update_document_name_only(document_id, req_doc_name):
    """仅更新文档名称（不进行验证）

    Args:
        document_id: 文档 ID（字符串）
        req_doc_name: 请求中的新文档名称（字符串）

    Returns:
        成功时返回 None，失败时返回 JSON 格式的错误信息
    """
    if not DocumentService.update_by_id(document_id, {"name": req_doc_name}):
        return get_error_data_result(message="Database error (Document rename)!")

    informs = File2DocumentService.get_by_document_id(document_id)
    if informs:
        e, file = FileService.get_by_id(informs[0].file_id)
        FileService.update_by_id(file.id, {"name": req_doc_name})
    # Add logic to update index - refer to rename method in document_app.py
    tenant_id = DocumentService.get_tenant_id(document_id)
    title_tks = rag_tokenizer.tokenize(req_doc_name)
    es_body = {
        "docnm_kwd": req_doc_name,
        "title_tks": title_tks,
        "title_sm_tks": rag_tokenizer.fine_grained_tokenize(title_tks),
    }
    ok, doc = DocumentService.get_by_id(document_id)
    if not ok:
        return get_error_data_result(message=f"Not able to find document by id:{document_id}")
    if settings.docStoreConn.index_exist(search.index_name(tenant_id), doc.kb_id):
        settings.docStoreConn.update(
            {"doc_id": document_id},
            es_body,
            search.index_name(tenant_id),
            doc.kb_id,
        )
    return None

def update_chunk_method_only(req, doc, dataset_id, tenant_id):
    """仅更新分块方法（不进行验证）

    更新文档的分块方法和解析器配置，如果分块方法发生变化则重置文档进度。
    如果方法发生变化，还会清除文档存储中现有的分块。

    Args:
        req: 包含 chunk_method 和 parser_config 的请求字典
        doc: 数据库中的文档模型
        dataset_id: 包含该文档的数据集 ID
        tenant_id: 文档存储的租户 ID

    Returns:
        成功时返回 None，失败时返回错误结果字典
    """
    if doc.parser_id.lower() != req["chunk_method"].lower():
        # if chunk method changed
        e = DocumentService.update_by_id(
            doc.id,
            {
                "parser_id": req["chunk_method"],
                "progress": 0,
                "progress_msg": "",
                "run": TaskStatus.UNSTART.value,
            },
        )
        if not e:
            return get_error_data_result(message="Document not found!")
    if not req.get("parser_config"):
        req["parser_config"] = get_parser_config(req["chunk_method"], req.get("parser_config"))
        DocumentService.update_parser_config(doc.id, req["parser_config"])
    if doc.token_num > 0:
        e = DocumentService.increment_chunk_num(
            doc.id,
            doc.kb_id,
            doc.token_num * -1,
            doc.chunk_num * -1,
            doc.process_duration * -1,
            )
        if not e:
            return get_error_data_result(message="Document not found!")
        settings.docStoreConn.delete({"doc_id": doc.id}, search.index_name(tenant_id), dataset_id)
    return None

def update_document_status_only(status:int, doc, kb):
    """仅更新文档状态（不进行验证）

    更新文档的启用/禁用状态，并更新文档存储中的相应索引。

    Args:
        status: 新的状态值（0 表示禁用，1 表示启用）
        doc: 数据库中的文档模型
        kb: 知识库模型

    Returns:
        成功时返回 None，失败时返回错误结果字典
    """
    if doc.status is None or (int(doc.status) != status):
        try:
            if not DocumentService.update_by_id(doc.id, {"status": str(status)}):
                return get_error_data_result(message="Database error (Document update)!")
            settings.docStoreConn.update({"doc_id": doc.id}, {"available_int": status}, search.index_name(kb.tenant_id), doc.kb_id)
        except Exception as e:
            return server_error_response(e)
    return None


def validate_document_update_fields(update_doc_req:UpdateDocumentReq, doc, req):
    """在单个方法中验证文档更新字段

    对所有文档更新字段进行综合验证，包括不可变字段、文档名称和分块方法。

    Args:
        update_doc_req: 经过验证的更新文档请求
        doc: 数据库中的文档模型
        req: 原始请求字典

    Returns:
        验证失败时返回 (错误消息, 错误代码) 元组，
        验证通过时返回 (None, None)
    """
    # Validate immutable fields
    error_msg, error_code = validation_utils.validate_immutable_fields(update_doc_req, doc)
    if error_msg:
        return error_msg, error_code

    # Validate document name if present
    if "name" in req and req["name"] != doc.name:
        docs_from_name = DocumentService.query(name=req["name"], kb_id=doc.kb_id)
        error_msg, error_code = validation_utils.validate_document_name(req["name"], doc, docs_from_name)
        if error_msg:
            return error_msg, error_code

    # Validate chunk method if present
    if "chunk_method" in req:
        error_msg, error_code = validation_utils.validate_chunk_method(doc, req["chunk_method"])
        if error_msg:
            return error_msg, error_code

    return None, None

def rename_doc_key(doc):
    """重命名文档键以匹配 API 响应格式

    将内部文档模型字段名称转换为外部 API 响应字段名称
    （例如 'chunk_num' -> 'chunk_count'）。

    Args:
        doc: 数据库中的文档模型

    Returns:
        包含重命名键的字典，用于 API 响应
    """
    key_mapping = {
        "chunk_num": "chunk_count",
        "kb_id": "dataset_id",
        "token_num": "token_count",
        "parser_id": "chunk_method",
    }
    run_mapping = {
        "0": "UNSTART",
        "1": "RUNNING",
        "2": "CANCEL",
        "3": "DONE",
        "4": "FAIL",
    }
    renamed_doc = {}
    for key, value in doc.to_dict().items():
        new_key = key_mapping.get(key, key)
        renamed_doc[new_key] = value
        if key == "run":
            renamed_doc["run"] = run_mapping.get(str(value))
    return renamed_doc

