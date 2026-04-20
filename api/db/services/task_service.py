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
任务服务模块

本模块提供文档处理任务管理的核心业务逻辑，包括：
- 任务创建、进度跟踪、状态更新
- 文档解析任务的生命周期管理
- 任务重试机制
- Chunk（文档切片）管理
- 与文档服务的集成
- 任务队列管理

核心类：
- TaskService: 任务服务类，处理文档解析任务的创建、更新和查询
"""
import logging
import os
import random
import xxhash
from datetime import datetime

from api.db.db_utils import bulk_insert_into_db
from deepdoc.parser import PdfParser
from peewee import JOIN
from api.db.db_models import DB, File2Document, File
from api.db import FileType
from api.db.db_models import Task, Document, Knowledgebase, Tenant
from api.db.services.common_service import CommonService
from api.db.services.document_service import DocumentService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from common.constants import StatusEnum, TaskStatus
from deepdoc.parser.excel_parser import RAGFlowExcelParser
from rag.utils.redis_conn import REDIS_CONN
from common import settings
from rag.nlp import search

# Canvas 调试模式的虚拟文档 ID
CANVAS_DEBUG_DOC_ID = "dataflow_x"
# GraphRaptor 的虚拟文档 ID
GRAPH_RAPTOR_FAKE_DOC_ID = "graph_raptor_x"


def trim_header_by_lines(text: str, max_length) -> str:
    """
    按行裁剪文本到最大长度

    此函数用于裁剪过长的进度消息，优先保留较新的内容。
    它会查找换行符，在不超过最大长度的前提下，尽可能保留完整的行。

    逻辑说明：
    1. 如果文本长度不超过最大长度，直接返回原文本
    2. 从前往后遍历文本，查找换行符
    3. 当找到一个换行符，且裁剪后的剩余文本长度不超过 max_length 时，
       从该换行符之后截断，保留较新的内容

    :param text: 要裁剪的输入文本
    :param max_length: 允许的最大长度
    :return: 裁剪后的文本

    示例：
        text = "line1\\nline2\\nline3\\nline4"
        max_length = 10
        -> 返回 "line4"（从最后一个换行符后开始）
    """
    len_text = len(text)
    # 如果文本长度在限制范围内，无需裁剪
    if len_text <= max_length:
        return text

    # 遍历文本查找合适的截断点
    for i in range(len_text):
        # 找到换行符，且裁剪后的剩余长度不超过限制
        if text[i] == '\n' and len_text - i <= max_length:
            # 从换行符之后截断，保留较新的内容
            return text[i + 1:]

    # 如果找不到合适的截断点，返回原文本
    return text


class TaskService(CommonService):
    """
    任务服务类

    继承自 CommonService，提供文档处理任务的专门功能。
    负责管理文档解析任务的完整生命周期，包括创建、更新、查询和删除。

    主要功能：
    - 任务创建与队列管理
    - 任务进度跟踪与更新
    - 任务状态管理（运行中、已完成、已取消等）
    - Chunk（文档切片）ID 管理
    - 任务重试机制（最多 3 次）
    - 与文档、知识库、租户的关联查询

    Attributes:
        model: Task 数据库模型类
    """
    model = Task

    @classmethod
    @DB.connection_context()
    def get_task(cls, task_id, doc_ids=[]):
        """
        根据 task_id 获取任务详细信息

        此方法获取任务的完整信息，包括关联的文档、知识库和租户信息。
        同时处理任务重试逻辑和进度更新。

        逻辑说明：
        1. 处理特殊文档 ID（Canvas 调试模式）
        2. 构建多表联查查询（Task -> Document -> Knowledgebase -> Tenant）
        3. 获取任务详情
        4. 更新任务的进度消息和重试次数
        5. 如果重试次数超过 3 次，标记任务为已放弃
        6. 返回任务详情或 None（如果任务不存在或已超过重试限制）

        :param task_id: 要获取的任务 ID
        :param doc_ids: 文档 ID 列表（用于 Canvas 调试模式）
        :return: 包含所有任务信息和相关元数据的字典，如果任务未找到或超过重试限制则返回 None
        """
        # 获取任务的文档 ID
        doc_id = cls.model.doc_id

        # 处理 Canvas 调试模式的特殊情况
        if doc_id == CANVAS_DEBUG_DOC_ID and doc_ids:
            doc_id = doc_ids[0]

        # 定义要查询的字段列表（包含任务、文档、知识库、租户的相关字段）
        fields = [
            cls.model.id,                    # 任务 ID
            cls.model.doc_id,                # 文档 ID
            cls.model.from_page,             # 起始页
            cls.model.to_page,               # 结束页
            cls.model.retry_count,           # 重试次数
            Document.kb_id,                  # 知识库 ID
            Document.parser_id,              # 解析器 ID
            Document.parser_config,          # 解析器配置
            Document.name,                   # 文档名称
            Document.type,                   # 文档类型
            Document.location,               # 文档位置
            Document.size,                   # 文档大小
            Knowledgebase.tenant_id,         # 租户 ID
            Knowledgebase.language,          # 语言设置
            Knowledgebase.embd_id,           # 嵌入模型 ID
            Knowledgebase.pagerank,          # 页面排名配置
            Knowledgebase.parser_config.alias("kb_parser_config"),  # 知识库解析器配置（使用别名避免冲突）
            Tenant.img2txt_id,               # 图像转文本模型 ID
            Tenant.asr_id,                   # 语音识别模型 ID
            Tenant.llm_id,                   # LLM 模型 ID
            cls.model.update_time,           # 更新时间
        ]

        # 构建多表联查查询
        # Task JOIN Document ON task.doc_id = document.id
        # Document JOIN Knowledgebase ON document.kb_id = knowledgebase.id
        # Knowledgebase JOIN Tenant ON knowledgebase.tenant_id = tenant.id
        docs = (
            cls.model.select(*fields)
                .join(Document, on=(doc_id == Document.id))
                .join(Knowledgebase, on=(Document.kb_id == Knowledgebase.id))
                .join(Tenant, on=(Knowledgebase.tenant_id == Tenant.id))
                .where(cls.model.id == task_id)
        )

        # 将查询结果转换为字典列表
        docs = list(docs.dicts())

        # 如果没有找到任务，返回 None
        if not docs:
            return None

        # 构建进度消息
        msg = f"\n{datetime.now().strftime('%H:%M:%S')} Task has been received."
        # 生成一个小的随机进度值（0.0-0.1），表示任务已被接收
        prog = random.random() / 10.0

        # 检查重试次数是否超过限制
        if docs[0]["retry_count"] >= 3:
            # 超过重试限制，标记任务为已放弃
            msg = "\nERROR: Task is abandoned after 3 times attempts."
            prog = -1  # 负数表示任务失败

        # 更新任务的进度消息、进度值和重试次数
        cls.model.update(
            progress_msg=cls.model.progress_msg + msg,
            progress=prog,
            retry_count=docs[0]["retry_count"] + 1,
        ).where(cls.model.id == docs[0]["id"]).execute()

        # 如果重试次数超过限制，返回 None
        if docs[0]["retry_count"] >= 3:
            return None

        # 返回任务详情
        return docs[0]

    @classmethod
    @DB.connection_context()
    def get_tasks(cls, doc_id: str):
        """
        获取文档关联的所有任务

        此方法获取指定文档的所有处理任务，按页码和创建时间排序。

        逻辑说明：
        1. 选择任务相关字段（ID、页码、进度、摘要、chunk_ids）
        2. 按起始页升序、创建时间降序排序
        3. 过滤指定文档的任务
        4. 返回任务列表或 None

        :param doc_id: 文档的唯一标识符
        :return: 任务字典列表，如果没有找到任务则返回 None
        """
        # 定义要查询的字段
        fields = [
            cls.model.id,          # 任务 ID
            cls.model.from_page,   # 起始页
            cls.model.progress,    # 进度（0.0-1.0）
            cls.model.digest,      # 任务摘要（用于任务去重和优化）
            cls.model.chunk_ids,   # 关联的 chunk ID 列表
        ]

        # 构建查询：按起始页升序、创建时间降序排序
        tasks = (
            cls.model.select(*fields).order_by(cls.model.from_page.asc(), cls.model.create_time.desc())
            .where(cls.model.doc_id == doc_id)
        )

        # 转换为字典列表
        tasks = list(tasks.dicts())

        # 如果没有任务，返回 None
        if not tasks:
            return None

        return tasks

    @classmethod
    @DB.connection_context()
    def get_tasks_progress_by_doc_ids(cls, doc_ids: list[str]):
        """
        根据文档 ID 列表获取任务进度

        此方法获取多个文档的处理任务，按创建时间倒序排列。

        逻辑说明：
        1. 选择任务相关字段（包含进度消息）
        2. 按创建时间倒序排序
        3. 使用 IN 子句过滤多个文档
        4. 返回任务列表或 None

        :param doc_ids: 文档 ID 列表
        :return: 任务字典列表，如果没有找到任务则返回 None
        """
        # 定义要查询的字段（包含进度消息）
        fields = [
            cls.model.id,              # 任务 ID
            cls.model.doc_id,          # 文档 ID
            cls.model.from_page,       # 起始页
            cls.model.progress,        # 进度
            cls.model.progress_msg,    # 进度消息
            cls.model.digest,          # 任务摘要
            cls.model.chunk_ids,       # chunk ID 列表
            cls.model.create_time      # 创建时间
        ]

        # 构建查询：按创建时间倒序排序
        tasks = (
            cls.model.select(*fields).order_by(cls.model.create_time.desc())
            .where(cls.model.doc_id.in_(doc_ids))
        )

        # 转换为字典列表
        tasks = list(tasks.dicts())

        # 如果没有任务，返回 None
        if not tasks:
            return None

        return tasks

    @classmethod
    @DB.connection_context()
    def update_chunk_ids(cls, id: str, chunk_ids: str):
        """
        更新任务关联的 chunk ID 列表

        此方法更新任务的 chunk_ids 字段，存储已处理文档切片的 ID。
        chunk_ids 以空格分隔的字符串格式存储。

        逻辑说明：
        1. 构建 UPDATE 语句
        2. 设置 chunk_ids 字段
        3. WHERE 条件为任务 ID 匹配
        4. 执行更新

        :param id: 任务的唯一标识符
        :param chunk_ids: 空格分隔的 chunk 标识符字符串
        """
        cls.model.update(chunk_ids=chunk_ids).where(cls.model.id == id).execute()

    @classmethod
    @DB.connection_context()
    def get_ongoing_doc_name(cls):
        """
        获取正在处理的文档名称

        此方法检索处于处理状态的文档信息，包括它们的位置和关联 ID。
        使用数据库锁确保线程安全。

        逻辑说明：
        1. 使用数据库锁（"get_task"）确保线程安全
        2. 联查 Task、Document、File2Document、File 表
        3. 过滤条件：
           - 文档状态为 VALID
           - 文档运行状态为 RUNNING
           - 文档类型不是 VIRTUAL
           - 任务进度小于 1（未完成）
           - 任务创建时间在 10 分钟内
        4. 返回去重后的 (parent_id/kb_id, location) 元组列表

        :return: 元组列表，每个元组包含 (parent_id/kb_id, location)，如果没有文档在处理则返回空列表
        """
        # 使用数据库锁确保线程安全
        with DB.lock("get_task", -1):
            # 构建多表联查查询
            docs = (
                cls.model.select(
                    *[Document.id, Document.kb_id, Document.location, File.parent_id]
                )
                # JOIN Document 表
                .join(Document, on=(cls.model.doc_id == Document.id))
                # LEFT JOIN File2Document 表（可能有文件关联）
                .join(
                    File2Document,
                    on=(File2Document.document_id == Document.id),
                    join_type=JOIN.LEFT_OUTER,
                )
                # LEFT JOIN File 表（获取文件信息）
                .join(
                    File,
                    on=(File2Document.file_id == File.id),
                    join_type=JOIN.LEFT_OUTER,
                )
                # 应用过滤条件
                .where(
                    Document.status == StatusEnum.VALID.value,          # 文档状态有效
                    Document.run == TaskStatus.RUNNING.value,            # 文档正在运行
                    ~(Document.type == FileType.VIRTUAL.value),          # 排除虚拟文档
                    cls.model.progress < 1,                              # 任务未完成
                    cls.model.create_time >= current_timestamp() - 1000 * 600,  # 10分钟内创建的任务
                )
            )

            # 转换为字典列表
            docs = list(docs.dicts())

            # 如果没有找到文档，返回空列表
            if not docs:
                return []

            # 返回去重后的结果
            # 使用 parent_id（如果存在）或 kb_id 作为第一个元素
            return list(
                set(
                    [
                        (
                            d["parent_id"] if d["parent_id"] else d["kb_id"],
                            d["location"],
                        )
                        for d in docs
                    ]
                )
            )

    @classmethod
    @DB.connection_context()
    def do_cancel(cls, id):
        """
        检查任务是否应该被取消

        此方法通过检查关联文档的运行状态和进度来确定任务是否应该取消。

        逻辑说明：
        1. 根据 ID 获取任务
        2. 获取任务关联的文档
        3. 检查文档的运行状态是否为 CANCEL
        4. 检查文档的进度是否为负数（表示失败）
        5. 返回是否应该取消任务

        :param id: 要检查的任务的唯一标识符
        :return: 如果任务应该取消则返回 True，否则返回 False
        """
        # 获取任务对象
        task = cls.model.get_by_id(id)

        # 获取任务关联的文档
        _, doc = DocumentService.get_by_id(task.doc_id)

        # 检查文档是否被标记为取消，或进度为负（表示失败）
        return doc.run == TaskStatus.CANCEL.value or doc.progress < 0

    @classmethod
    @DB.connection_context()
    def update_progress(cls, id, info):
        """
        更新任务的进度信息

        此方法更新任务的进度消息和完成百分比。
        处理平台特定行为（macOS vs 其他）并在必要时使用数据库锁确保线程安全。

        更新规则：
        - progress_msg: 始终将新消息追加到现有消息，并裁剪结果到最多 3000 行
        - progress: 在以下情况下更新：
          (a) 新进度 >= 1（允许从 -1 恢复）
          (b) 当前进度 != -1 且（新进度为 -1 或大于现有进度）

        逻辑说明：
        1. 获取任务对象
        2. 检查是否为 macOS 环境（macOS 不支持数据库锁）
        3. 根据 platform 选择不同的更新策略
        4. 更新进度消息（追加新消息并裁剪）
        5. 更新进度值（根据规则）
        6. 计算并更新处理时长

        :param id: 要更新的任务的唯一标识符
        :param info: 包含进度信息的字典，键包括：
                    - progress_msg (str, optional): 要追加的进度消息
                    - progress (float, optional): 进度百分比（0.0 到 1.0）
        """
        # 获取任务对象
        task = cls.model.get_by_id(id)

        # 如果任务不存在，记录警告并返回
        if not task:
            logging.warning("Update_progress error: task not found")
            return

        # 检查是否为 macOS 环境
        if os.environ.get("MACOS"):
            # macOS 不支持数据库锁，直接更新
            # 更新进度消息
            if info["progress_msg"]:
                # 追加新消息并裁剪到最多 3000 行
                progress_msg = trim_header_by_lines(task.progress_msg + "\n" + info["progress_msg"], 3000)
                cls.model.update(progress_msg=progress_msg).where(cls.model.id == id).execute()

            # 更新进度值
            if "progress" in info:
                prog = info["progress"]
                # 使用复杂的 WHERE 条件确保只在必要时更新
                # 条件：(prog >= 1) OR (progress != -1 AND (prog == -1 OR prog > progress))
                cls.model.update(progress=prog).where(
                    (cls.model.id == id) &
                    ((prog >= 1) | ((cls.model.progress != -1) &
                    ((prog == -1) | (prog > cls.model.progress))))
                ).execute()
        else:
            # 其他平台使用数据库锁确保线程安全
            with DB.lock("update_progress", -1):
                # 更新进度消息
                if info["progress_msg"]:
                    progress_msg = trim_header_by_lines(task.progress_msg + "\n" + info["progress_msg"], 3000)
                    cls.model.update(progress_msg=progress_msg).where(cls.model.id == id).execute()

                # 更新进度值
                if "progress" in info:
                    prog = info["progress"]
                    cls.model.update(progress=prog).where(
                        (cls.model.id == id) &
                        ((prog >= 1) | ((cls.model.progress != -1) &
                        ((prog == -1) | (prog > cls.model.progress))))
                    ).execute()

        # 计算并更新处理时长（从任务开始到现在的秒数）
        process_duration = (datetime.now() - task.begin_at).total_seconds()
        cls.model.update(process_duration=process_duration).where(cls.model.id == id).execute()

    @classmethod
    @DB.connection_context()
    def delete_by_doc_ids(cls, doc_ids):
        """
        根据文档 ID 删除关联的任务

        此方法删除指定文档的所有关联任务。

        逻辑说明：
        1. 构建 DELETE 语句
        2. 使用 IN 子句匹配多个文档 ID
        3. 执行删除并返回受影响的行数

        :param doc_ids: 文档 ID 列表
        :return: 删除的任务数量
        """
        return cls.model.delete().where(cls.model.doc_id.in_(doc_ids)).execute()


def queue_tasks(doc: dict, bucket: str, name: str, priority: int):
    """
    创建并排队文档处理任务

    此函数根据文档类型和配置创建处理任务。
    处理不同类型的文档（PDF、Excel 等）并管理任务分块和配置。
    通过检查先前完成的任务实现任务重用优化。

    逻辑说明：
    1. 根据文档类型创建任务：
       - PDF: 按页码范围创建任务
       - Excel: 按行数范围创建任务
       - 其他: 创建单个任务
    2. 计算任务摘要（用于任务去重和优化）
    3. 尝试重用之前任务的 chunks
    4. 删除旧任务和相关的 chunks
    5. 插入新任务到数据库
    6. 将未完成的任务加入 Redis 队列

    :param doc: 包含元数据和配置的文档字典
    :param bucket: 存储文档的存储桶名称
    :param name: 文档的文件名
    :param priority: 任务队列的优先级

    Note:
        - 对于 PDF 文档，根据配置按页码范围创建任务
        - 对于 Excel 文档，按行数范围创建任务
        - 计算任务摘要用于优化和重用
        - 如果可用，可能会重用之前的任务 chunks
    """
    # 内部函数：创建新任务模板
    def new_task():
        return {
            "id": get_uuid(),              # 生成唯一任务 ID
            "doc_id": doc["id"],           # 关联文档 ID
            "progress": 0.0,               # 初始进度为 0
            "from_page": 0,                # 起始页（默认为 0）
            "to_page": 100000000,          # 结束页（默认为很大的值）
            "begin_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 开始时间
        }

    # 初始化任务数组
    parse_task_array = []

    # 根据文档类型创建任务
    if doc["type"] == FileType.PDF.value:
        # PDF 文档处理
        # 从存储获取文件内容
        file_bin = settings.STORAGE_IMPL.get(bucket, name)

        # 获取布局识别配置
        do_layout = doc["parser_config"].get("layout_recognize", "DeepDOC")

        # 获取 PDF 总页数
        pages = PdfParser.total_page_number(doc["name"], file_bin)
        if pages is None:
            pages = 0

        # 获取每批处理的页数
        page_size = doc["parser_config"].get("task_page_size") or 12

        # 论文类型的文档使用更大的页数批次
        if doc["parser_id"] == "paper":
            page_size = doc["parser_config"].get("task_page_size") or 22

        # 特殊解析器或不需要布局识别时，处理整个文档
        if doc["parser_id"] in ["one", "knowledge_graph"] or do_layout != "DeepDOC" or doc["parser_config"].get("toc_extraction", False):
            page_size = 10 ** 9  # 设置为很大的值，表示处理整个文档

        # 获取用户指定的页码范围
        page_ranges = doc["parser_config"].get("pages") or [(1, 10 ** 5)]

        # 为每个页码范围创建任务
        for s, e in page_ranges:
            # 调整页码（从 1-based 转为 0-based）
            s -= 1
            s = max(0, s)  # 确保起始页不小于 0
            e = min(e - 1, pages)  # 确保结束页不超过总页数

            # 按 page_size 分批创建任务
            for p in range(s, e, page_size):
                task = new_task()
                task["from_page"] = p
                task["to_page"] = min(p + page_size, e)
                parse_task_array.append(task)

    elif doc["parser_id"] == "table":
        # Excel 表格处理
        file_bin = settings.STORAGE_IMPL.get(bucket, name)
        # 获取表格总行数
        rn = RAGFlowExcelParser.row_number(doc["name"], file_bin)

        # 每 3000 行创建一个任务
        for i in range(0, rn, 3000):
            task = new_task()
            task["from_page"] = i  # 这里使用 from_page 存储起始行
            task["to_page"] = min(i + 3000, rn)  # 这里使用 to_page 存储结束行
            parse_task_array.append(task)
    else:
        # 其他类型的文档，创建单个任务
        parse_task_array.append(new_task())

    # 获取分块配置
    chunking_config = DocumentService.get_chunking_config(doc["id"])

    # 为每个任务计算摘要（用于任务去重和优化）
    for task in parse_task_array:
        # 使用 xxhash 计算配置的哈希值
        hasher = xxhash.xxh64()

        # 对分块配置的所有字段进行哈希
        for field in sorted(chunking_config.keys()):
            if field == "parser_config":
                # 移除不影响 chunk 的配置项（raptor 和 graphrag）
                for k in ["raptor", "graphrag"]:
                    if k in chunking_config[field]:
                        del chunking_config[field][k]
            # 更新哈希值
            hasher.update(str(chunking_config[field]).encode("utf-8"))

        # 对任务的关键字段进行哈希
        for field in ["doc_id", "from_page", "to_page"]:
            hasher.update(str(task.get(field, "")).encode("utf-8"))

        # 生成任务摘要
        task_digest = hasher.hexdigest()
        task["digest"] = task_digest
        task["progress"] = 0.0
        task["priority"] = priority

    # 尝试重用之前任务的 chunks
    prev_tasks = TaskService.get_tasks(doc["id"])
    ck_num = 0  # 重用的 chunk 数量

    if prev_tasks:
        # 遍历新任务，尝试重用旧任务的 chunks
        for task in parse_task_array:
            ck_num += reuse_prev_task_chunks(task, prev_tasks, chunking_config)

        # 删除旧任务
        TaskService.filter_delete([Task.doc_id == doc["id"]])

        # 收集旧任务的 chunk IDs
        pre_chunk_ids = []
        for pre_task in prev_tasks:
            if pre_task["chunk_ids"]:
                pre_chunk_ids.extend(pre_task["chunk_ids"].split())

        # 从文档存储中删除旧的 chunks
        if pre_chunk_ids:
            settings.docStoreConn.delete(
                {"id": pre_chunk_ids},
                search.index_name(chunking_config["tenant_id"]),
                chunking_config["kb_id"]
            )

    # 更新文档的 chunk 数量
    DocumentService.update_by_id(doc["id"], {"chunk_num": ck_num})

    # 批量插入新任务到数据库
    bulk_insert_into_db(Task, parse_task_array, True)

    # 标记文档开始解析
    DocumentService.begin2parse(doc["id"])

    # 将未完成的任务加入 Redis 队列
    unfinished_task_array = [task for task in parse_task_array if task["progress"] < 1.0]
    for unfinished_task in unfinished_task_array:
        # 将任务加入对应优先级的队列
        assert REDIS_CONN.queue_product(
            settings.get_svr_queue_name(priority), message=unfinished_task
        ), "Can't access Redis. Please check the Redis' status."


def reuse_prev_task_chunks(task: dict, prev_tasks: list[dict], chunking_config: dict):
    """
    尝试重用之前任务的 chunks 以优化性能

    此函数检查之前已完成任务的 chunks 是否可以重用于当前任务，
        这可以显著提高处理效率。基于页码范围和配置摘要匹配任务。

    逻辑说明：
    1. 在 prev_tasks 中查找页码范围和配置摘要匹配的任务
    2. 如果找到匹配的任务：
       - 检查旧任务是否完成（progress = 1.0）
       - 检查旧任务是否有 chunk_ids
       - 如果满足条件，重用 chunk_ids
       - 设置当前任务进度为 1.0
       - 生成重用消息
       - 清空旧任务的 chunk_ids（避免重复）
    3. 返回重用的 chunk 数量

    :param task: 当前任务字典
    :param prev_tasks: 之前的任务字典列表
    :param chunking_config: 分块配置字典
    :return: 成功重用的 chunk 数量，如果不能重用则返回 0

    Note:
        只有在以下情况下才能重用 chunks：
        - 存在页码范围和配置摘要匹配的之前任务
        - 之前任务成功完成（progress = 1.0）
        - 之前任务有有效的 chunk IDs
    """
    # 在 prev_tasks 中查找匹配的任务
    idx = 0
    while idx < len(prev_tasks):
        prev_task = prev_tasks[idx]
        # 检查页码范围和摘要是否匹配
        if prev_task.get("from_page", 0) == task.get("from_page", 0) \
                and prev_task.get("digest", 0) == task.get("digest", ""):
            break  # 找到匹配的任务
        idx += 1

    # 如果没有找到匹配的任务，返回 0
    if idx >= len(prev_tasks):
        return 0

    # 获取匹配的旧任务
    prev_task = prev_tasks[idx]

    # 检查旧任务是否完成且有 chunks
    if prev_task["progress"] < 1.0 or not prev_task["chunk_ids"]:
        return 0

    # 重用旧任务的 chunks
    task["chunk_ids"] = prev_task["chunk_ids"]
    task["progress"] = 1.0  # 标记为已完成

    # 生成进度消息
    if "from_page" in task and "to_page" in task and int(task['to_page']) - int(task['from_page']) >= 10 ** 6:
        # 如果页码范围很大（表示处理整个文档）
        task["progress_msg"] = f"Page({task['from_page']}~{task['to_page']}): "
    else:
        task["progress_msg"] = ""

    # 添加时间戳和重用消息
    task["progress_msg"] = " ".join(
        [datetime.now().strftime("%H:%M:%S"), task["progress_msg"], "Reused previous task's chunks."])

    # 清空旧任务的 chunk_ids（避免重复计数）
    prev_task["chunk_ids"] = ""

    # 返回重用的 chunk 数量
    return len(task["chunk_ids"].split())


def cancel_all_task_of(doc_id):
    """
    取消文档的所有任务

    此函数通过在 Redis 中设置取消标志来取消文档的所有关联任务。

    逻辑说明：
    1. 查询文档的所有任务
    2. 为每个任务在 Redis 中设置取消标志
    3. 捕获并记录异常

    :param doc_id: 文档 ID
    """
    # 查询文档的所有任务
    for t in TaskService.query(doc_id=doc_id):
        try:
            # 在 Redis 中设置取消标志
            REDIS_CONN.set(f"{t.id}-cancel", "x")
        except Exception as e:
            # 记录异常
            logging.exception(e)


def has_canceled(task_id):
    """
    检查任务是否已被取消

    此函数通过检查 Redis 中的取消标志来判断任务是否已被取消。

    逻辑说明：
    1. 从 Redis 获取取消标志
    2. 如果标志存在，记录日志并返回 True
    3. 如果发生异常，记录异常并返回 False

    :param task_id: 任务 ID
    :return: 如果任务已取消则返回 True，否则返回 False
    """
    try:
        # 检查 Redis 中是否存在取消标志
        if REDIS_CONN.get(f"{task_id}-cancel"):
            logging.info(f"Task: {task_id} has been canceled")
            return True
    except Exception as e:
        # 记录异常
        logging.exception(e)

    return False


def queue_dataflow(
    tenant_id: str,
    flow_id: str,
    task_id: str,
    doc_id: str = CANVAS_DEBUG_DOC_ID,
    file: dict = None,
    priority: int = 0,
    rerun: bool = False
) -> tuple[bool, str]:
    """
    创建并排队数据流处理任务

    此函数创建数据流（Canvas/工作流）处理任务并将其加入队列。

    逻辑说明：
    1. 构建任务字典
    2. 如果不是虚拟文档，删除旧任务并标记文档开始解析
    3. 插入任务到数据库
    4. 添加知识库和租户信息
    5. 将任务加入 Redis 队列
    6. 返回结果

    :param tenant_id: 租户 ID
    :param flow_id: 数据流 ID
    :param task_id: 任务 ID
    :param doc_id: 文档 ID（默认为虚拟文档 ID）
    :param file: 文件信息字典
    :param priority: 任务优先级
    :param rerun: 是否为重新运行
    :return: (成功标志, 错误消息) 元组
    """
    # 构建任务字典
    task = dict(
        id=task_id,                                   # 任务 ID
        doc_id=doc_id,                                # 文档 ID
        from_page=0,                                  # 起始页
        to_page=100000000,                            # 结束页
        task_type="dataflow" if not rerun else "dataflow_rerun",  # 任务类型
        priority=priority,                            # 优先级
        begin_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 开始时间
    )

    # 如果不是虚拟文档，需要清理旧任务
    if doc_id not in [CANVAS_DEBUG_DOC_ID, GRAPH_RAPTOR_FAKE_DOC_ID]:
        # 删除文档的旧任务
        TaskService.model.delete().where(TaskService.model.doc_id == doc_id).execute()
        # 标记文档开始解析
        DocumentService.begin2parse(doc_id)

    # 插入任务到数据库（支持冲突时替换）
    bulk_insert_into_db(model=Task, data_source=[task], replace_on_conflict=True)

    # 添加知识库和租户信息
    task["kb_id"] = DocumentService.get_knowledgebase_id(doc_id)
    task["tenant_id"] = tenant_id
    task["dataflow_id"] = flow_id
    task["file"] = file

    # 将任务加入 Redis 队列
    if not REDIS_CONN.queue_product(
            settings.get_svr_queue_name(priority), message=task
    ):
        return False, "Can't access Redis. Please check the Redis' status."

    return True, ""
