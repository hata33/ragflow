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

import time


from common.misc_utils import thread_pool_exec

# 记录进程启动时间戳，用于计算总初始化耗时
start_ts = time.time()

import asyncio
import socket
import concurrent
# from beartype import BeartypeConf
# from beartype.claw import beartype_all  # <-- you didn't sign up for this
# beartype_all(conf=BeartypeConf(violation_type=UserWarning))    # <-- emit warnings from all code
import random
import sys
import threading

from api.db import PIPELINE_SPECIAL_PROGRESS_FREEZE_TASK_TYPES
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.pipeline_operation_log_service import PipelineOperationLogService
from api.db.joint_services.memory_message_service import handle_save_to_memory_task
from common.connection_utils import timeout
from common.metadata_utils import turn2jsonschema, update_metadata_to
from rag.utils.base64_image import image2id
from rag.utils.raptor_utils import should_skip_raptor, get_skip_reason
from common.log_utils import init_root_logger
from common.config_utils import show_configs
from rag.graphrag.general.index import run_graphrag_for_kb
from rag.graphrag.utils import get_llm_cache, set_llm_cache, get_tags_from_cache, set_tags_to_cache
from rag.prompts.generator import keyword_extraction, question_proposal, content_tagging, run_toc_from_text, \
    gen_metadata
import logging
import os
from datetime import datetime
import json
import xxhash
import copy
import re
from functools import partial
from multiprocessing.context import TimeoutError
from timeit import default_timer as timer
import signal
import exceptiongroup
import faulthandler
import numpy as np
from peewee import DoesNotExist
from common.constants import LLMType, ParserType, PipelineTaskType
from api.db.services.document_service import DocumentService
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.llm_service import LLMBundle
from api.db.services.task_service import TaskService, has_canceled, CANVAS_DEBUG_DOC_ID, GRAPH_RAPTOR_FAKE_DOC_ID
from api.db.services.file2document_service import File2DocumentService
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name, get_tenant_default_model_by_type
from common.versions import get_ragflow_version
from api.db.db_models import close_connection
from rag.app import laws, paper, presentation, manual, qa, table, book, resume, picture, naive, one, audio, \
    email, tag
from rag.nlp import search, rag_tokenizer, add_positions
from rag.raptor import RecursiveAbstractiveProcessing4TreeOrganizedRetrieval as Raptor
from common.token_utils import num_tokens_from_string, truncate
from rag.utils.redis_conn import REDIS_CONN, RedisDistributedLock
from rag.graphrag.utils import chat_limiter
from common.signal_utils import start_tracemalloc_and_snapshot, stop_tracemalloc
from common.exceptions import TaskCanceledException
from common import settings
from common.constants import PAGERANK_FLD, TAG_FLD, SVR_CONSUMER_GROUP_NAME

BATCH_SIZE = 64

# 解析器工厂映射表：将解析器类型（ParserType）映射到对应的解析器模块
# 每种文档类型（论文、书籍、简历、QA 等）都有专门的解析策略
FACTORY = {
    "general": naive,                              # 通用解析器（naive 的别名）
    ParserType.NAIVE.value: naive,                 # 通用/手动分块解析器
    ParserType.PAPER.value: paper,                 # 学术论文解析器
    ParserType.BOOK.value: book,                   # 书籍解析器
    ParserType.PRESENTATION.value: presentation,   # PPT/演示文稿解析器
    ParserType.MANUAL.value: manual,               # 使用手册解析器
    ParserType.LAWS.value: laws,                   # 法律文档解析器
    ParserType.QA.value: qa,                       # 问答对解析器
    ParserType.TABLE.value: table,                 # 表格解析器
    ParserType.RESUME.value: resume,               # 简历解析器
    ParserType.PICTURE.value: picture,             # 图片解析器
    ParserType.ONE.value: one,                     # 整文档作为单个 chunk 的解析器
    ParserType.AUDIO.value: audio,                 # 音频解析器
    ParserType.EMAIL.value: email,                 # 邮件解析器
    ParserType.KG.value: naive,                    # 知识图谱解析（复用 naive 解析器）
    ParserType.TAG.value: tag                      # 标签解析器
}

# 任务类型到流水线任务类型的映射，用于操作日志记录
TASK_TYPE_TO_PIPELINE_TASK_TYPE = {
    "dataflow": PipelineTaskType.PARSE,        # 数据流/解析任务
    "raptor": PipelineTaskType.RAPTOR,         # RAPTOR 递归摘要任务
    "graphrag": PipelineTaskType.GRAPH_RAG,    # GraphRAG 知识图谱任务
    "mindmap": PipelineTaskType.MINDMAP,       # 思维导图任务
    "memory": PipelineTaskType.MEMORY,         # 记忆保存任务
}

UNACKED_ITERATOR = None

# 消费者编号，从命令行参数获取，用于区分同一服务组的多个 worker 实例
CONSUMER_NO = "0" if len(sys.argv) < 2 else sys.argv[1]
# 消费者名称，格式为 "task_executor_<编号>"，用于 Redis 消费者组标识
CONSUMER_NAME = "task_executor_" + CONSUMER_NO
# Worker 启动时间，ISO 格式，用于心跳上报
BOOT_AT = datetime.now().astimezone().isoformat(timespec="milliseconds")
# 任务统计计数器
PENDING_TASKS = 0      # 待处理任务数
LAG_TASKS = 0          # 积压任务数
DONE_TASKS = 0         # 已完成任务数
FAILED_TASKS = 0       # 失败任务数

# 当前正在执行的任务字典 {task_id: task_info}，用于心跳上报当前状态
CURRENT_TASKS = {}

# 并发控制参数：通过环境变量配置各类资源的并发上限
MAX_CONCURRENT_TASKS = int(os.environ.get('MAX_CONCURRENT_TASKS', "5"))               # 最大并发任务数
MAX_CONCURRENT_CHUNK_BUILDERS = int(os.environ.get('MAX_CONCURRENT_CHUNK_BUILDERS', "1"))  # 最大并发 chunk 构建数
MAX_CONCURRENT_MINIO = int(os.environ.get('MAX_CONCURRENT_MINIO', '10'))               # 最大并发 MinIO 操作数

# 各类异步信号量，限制对应操作的并发数
task_limiter = asyncio.Semaphore(MAX_CONCURRENT_TASKS)           # 任务级并发限制
chunk_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS)  # chunk 构建（PDF 解析等）并发限制
embed_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS)  # 向量嵌入并发限制
minio_limiter = asyncio.Semaphore(MAX_CONCURRENT_MINIO)           # MinIO I/O 并发限制
kg_limiter = asyncio.Semaphore(2)                                  # 知识图谱任务并发限制（较重，限制为 2）
# Worker 心跳超时时间（秒），超过此时间未上报心跳的 worker 将被清理
WORKER_HEARTBEAT_TIMEOUT = int(os.environ.get('WORKER_HEARTBEAT_TIMEOUT', '120'))
# 线程安全的停止事件，用于优雅关闭
stop_event = threading.Event()


def signal_handler(sig, frame):
    """处理中断信号（SIGINT/SIGTERM），触发优雅关闭。

    设置停止事件标志，通知主循环退出，等待 1 秒后退出进程。
    """
    logging.info("Received interrupt signal, shutting down...")
    stop_event.set()
    time.sleep(1)
    sys.exit(0)


def set_progress(task_id, from_page=0, to_page=-1, prog=None, msg="Processing..."):
    """设置任务进度并更新到数据库，同时检查任务是否已被取消。

    进度消息格式：时间戳 + 页码范围 + 消息内容。
    当 prog < 0 时自动添加 [ERROR] 前缀标识错误。
    若任务已被取消（通过 Redis 标记），抛出 TaskCanceledException。

    Args:
        task_id: 任务 ID
        from_page: 起始页码（0-based）
        to_page: 结束页码（-1 表示不限制）
        prog: 进度值（0~1 为正常进度，-1 为错误，None 表示仅更新消息）
        msg: 进度消息文本

    Raises:
        TaskCanceledException: 当任务已被取消时抛出
    """
    try:
        if prog is not None and prog < 0:
            msg = "[ERROR]" + msg
        cancel = has_canceled(task_id)

        if cancel:
            msg += " [Canceled]"
            prog = -1

        if to_page > 0:
            if msg:
                if from_page < to_page:
                    msg = f"Page({from_page + 1}~{to_page + 1}): " + msg
        if msg:
            msg = datetime.now().strftime("%H:%M:%S") + " " + msg
        d = {"progress_msg": msg}
        if prog is not None:
            d["progress"] = prog

        TaskService.update_progress(task_id, d)

        close_connection()
        if cancel:
            raise TaskCanceledException(msg)
        logging.info(f"set_progress({task_id}), progress: {prog}, progress_msg: {msg}")
    except TaskCanceledException:
        raise
    except DoesNotExist:
        logging.warning(f"set_progress({task_id}) got exception DoesNotExist")
    except Exception as e:
        logging.exception(f"set_progress({task_id}), progress: {prog}, progress_msg: {msg}, got exception: {e}")


async def collect():
    """从 Redis 消息队列中消费一个任务。

    采用两级消费策略：
    1. 优先消费 UNACKED（已接收但未确认）的消息，避免消息丢失
    2. 若无 UNACKED 消息，则从 Redis Stream 中拉取新消息

    消费到消息后，根据任务类型（dataflow、memory、普通解析等）
    从数据库获取完整的任务信息，并检查任务是否已被取消。

    Returns:
        元组 (redis_msg, task)：
        - redis_msg: Redis 消息对象，用于后续 ACK 确认
        - task: 完整的任务字典，包含解析配置、文件信息等
        若无可用任务或任务已取消，返回 (None, None)
    """
    global CONSUMER_NAME, DONE_TASKS, FAILED_TASKS
    global UNACKED_ITERATOR

    svr_queue_names = settings.get_svr_queue_names()
    redis_msg = None
    try:
        if not UNACKED_ITERATOR:
            UNACKED_ITERATOR = REDIS_CONN.get_unacked_iterator(svr_queue_names, SVR_CONSUMER_GROUP_NAME, CONSUMER_NAME)
        try:
            redis_msg = next(UNACKED_ITERATOR)
        except StopIteration:
            for svr_queue_name in svr_queue_names:
                redis_msg = REDIS_CONN.queue_consumer(svr_queue_name, SVR_CONSUMER_GROUP_NAME, CONSUMER_NAME)
                if redis_msg:
                    break
    except Exception as e:
        logging.exception(f"collect got exception: {e}")
        return None, None

    if not redis_msg:
        return None, None
    msg = redis_msg.get_message()
    if not msg:
        logging.error(f"collect got empty message of {redis_msg.get_msg_id()}")
        redis_msg.ack()
        return None, None

    canceled = False
    # 根据任务来源类型，从不同的数据库表中获取完整的任务信息
    if msg.get("doc_id", "") in [GRAPH_RAPTOR_FAKE_DOC_ID, CANVAS_DEBUG_DOC_ID]:
        # GraphRAG 和 Canvas 调试任务使用特殊 doc_id，需从 task 表获取详情
        task = msg
        if task["task_type"] in PIPELINE_SPECIAL_PROGRESS_FREEZE_TASK_TYPES:
            task = TaskService.get_task(msg["id"], msg["doc_ids"])
            if task:
                task["doc_id"] = msg["doc_id"]
                task["doc_ids"] = msg.get("doc_ids", []) or []
    elif msg.get("task_type") == PipelineTaskType.MEMORY.lower():
        # 记忆保存任务，从 pipeline_task 表获取
        _, task_obj = TaskService.get_by_id(msg["id"])
        task = task_obj.to_dict()
    else:
        # 普通文档解析任务
        task = TaskService.get_task(msg["id"])

    # 检查任务是否已被取消或不存在
    if task:
        canceled = has_canceled(task["id"])
    if not task or canceled:
        state = "is unknown" if not task else "has been cancelled"
        FAILED_TASKS += 1
        logging.warning(f"collect task {msg['id']} {state}")
        redis_msg.ack()
        return None, None

    # 将消息中的额外字段补充到 task 对象中
    task_type = msg.get("task_type", "")
    task["task_type"] = task_type
    if task_type[:8] == "dataflow":
        # Dataflow 任务需要额外携带租户 ID、流水线 ID 和知识库 ID
        task["tenant_id"] = msg["tenant_id"]
        task["dataflow_id"] = msg["dataflow_id"]
        task["kb_id"] = msg.get("kb_id", "")
    if task_type[:6] == "memory":
        # Memory 任务需要携带记忆 ID、来源 ID 和消息内容
        task["memory_id"] = msg["memory_id"]
        task["source_id"] = msg["source_id"]
        task["message_dict"] = msg["message_dict"]
    return redis_msg, task


async def get_storage_binary(bucket, name):
    """从对象存储（MinIO）中异步获取文件的二进制数据。"""
    return await thread_pool_exec(settings.STORAGE_IMPL.get, bucket, name)


@timeout(60 * 80, 1)
async def build_chunks(task, progress_callback):
    """构建文档的 chunk（分块）数据，这是文档解析的核心流程。

    完整处理流程：
    1. 检查文件大小是否超出限制
    2. 从 MinIO 获取文件二进制数据
    3. 调用对应格式的解析器（chunker）进行分块
    4. 将 chunk 中的图片上传到 MinIO，生成图片 ID
    5. 可选：LLM 自动生成关键词（auto_keywords）
    6. 可选：LLM 自动生成问题（auto_questions）
    7. 可选：LLM 自动生成元数据（enable_metadata）
    8. 可选：对 chunk 进行自动标签分类（tag_kb_ids）

    Args:
        task: 任务字典，包含文件信息、解析配置等
        progress_callback: 进度回调函数

    Returns:
        docs: 处理后的 chunk 列表，每个 chunk 包含文本内容、向量、图片等信息
    """
    # 步骤 1：检查文件大小是否超出系统限制
    if task["size"] > settings.DOC_MAXIMUM_SIZE:
        set_progress(task["id"], prog=-1, msg="File size exceeds( <= %dMb )" %
                                              (int(settings.DOC_MAXIMUM_SIZE / 1024 / 1024)))
        return []

    # 根据解析器 ID 从工厂映射表获取对应的解析模块
    chunker = FACTORY[task["parser_id"].lower()]
    try:
        st = timer()
        bucket, name = File2DocumentService.get_storage_address(doc_id=task["doc_id"])
        binary = await get_storage_binary(bucket, name)
        logging.info("From minio({}) {}/{}".format(timer() - st, task["location"], task["name"]))
    except TimeoutError:
        progress_callback(-1, "Internal server error: Fetch file from minio timeout. Could you try it again.")
        logging.exception(
            "Minio {}/{} got timeout: Fetch file from minio timeout.".format(task["location"], task["name"]))
        raise
    except Exception as e:
        if re.search("(No such file|not found)", str(e)):
            progress_callback(-1, "Can not find file <%s> from minio. Could you try it again?" % task["name"])
        else:
            progress_callback(-1, "Get file from minio: %s" % str(e).replace("'", ""))
        logging.exception("Chunking {}/{} got exception".format(task["location"], task["name"]))
        raise

    try:
        # 步骤 3：调用解析器进行文档分块，受 chunk_limiter 并发控制
        async with chunk_limiter:
            cks = await thread_pool_exec(
                chunker.chunk,
                task["name"],
                binary=binary,
                from_page=task["from_page"],
                to_page=task["to_page"],
                lang=task["language"],
                callback=progress_callback,
                kb_id=task["kb_id"],
                parser_config=task["parser_config"],
                tenant_id=task["tenant_id"],
            )
        logging.info("Chunking({}) {}/{} done".format(timer() - st, task["location"], task["name"]))
    except TaskCanceledException:
        raise
    except Exception as e:
        progress_callback(-1, "Internal server error while chunking: %s" % str(e).replace("'", ""))
        logging.exception("Chunking {}/{} got exception".format(task["location"], task["name"]))
        raise

    # 步骤 4：构建 chunk 文档对象并上传图片到 MinIO
    docs = []
    # 每个 chunk 共享的基础文档信息
    doc = {
        "doc_id": task["doc_id"],
        "kb_id": str(task["kb_id"])
    }
    if task["pagerank"]:
        doc[PAGERANK_FLD] = int(task["pagerank"])
    st = timer()

    @timeout(60)
    async def upload_to_minio(document, chunk):
        """将单个 chunk 的图片上传到 MinIO 并生成唯一 ID。

        为 chunk 生成基于内容的 xxhash ID，处理图片上传逻辑：
        - 若 chunk 已有 img_id，直接添加到结果列表
        - 若 chunk 无图片，设置空 img_id
        - 若 chunk 有图片，调用 image2id 上传并获取图片 ID
        """
        try:
            d = copy.deepcopy(document)
            d.update(chunk)
            d["id"] = xxhash.xxh64(
                (chunk["content_with_weight"] + str(d["doc_id"])).encode("utf-8", "surrogatepass")).hexdigest()
            d["create_time"] = str(datetime.now()).replace("T", " ")[:19]
            d["create_timestamp_flt"] = datetime.now().timestamp()

            if d.get("img_id"):
                docs.append(d)
                return

            if not d.get("image"):
                _ = d.pop("image", None)
                d["img_id"] = ""
                docs.append(d)
                return
            await image2id(d, partial(settings.STORAGE_IMPL.put, tenant_id=task["tenant_id"]), d["id"], task["kb_id"])
            docs.append(d)
        except Exception:
            logging.exception(
                "Saving image of chunk {}/{}/{} got exception".format(task["location"], task["name"], d["id"]))
            raise

    tasks = []
    for ck in cks:
        tasks.append(asyncio.create_task(upload_to_minio(doc, ck)))
    try:
        await asyncio.gather(*tasks, return_exceptions=False)
    except Exception as e:
        logging.error(f"MINIO PUT({task['name']}) got exception: {e}")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    el = timer() - st
    logging.info("MINIO PUT({}) cost {:.3f} s".format(task["name"], el))

    # 步骤 5：可选 - 使用 LLM 自动提取每个 chunk 的关键词
    if task["parser_config"].get("auto_keywords", 0):
        st = timer()
        progress_callback(msg="Start to generate keywords for every chunk ...")
        chat_model_config = get_model_config_by_type_and_name(task["tenant_id"], LLMType.CHAT, task["llm_id"])
        chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])

        async def doc_keyword_extraction(chat_mdl, d, topn):
            """对单个 chunk 提取关键词，优先使用 LLM 缓存。"""
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "keywords", {"topn": topn})
            if not cached:
                if has_canceled(task["id"]):
                    progress_callback(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await keyword_extraction(chat_mdl, d["content_with_weight"], topn)
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "keywords", {"topn": topn})
            if cached:
                d["important_kwd"] = cached.split(",")
                d["important_tks"] = rag_tokenizer.tokenize(" ".join(d["important_kwd"]))
            return

        tasks = []
        for d in docs:
            tasks.append(
                asyncio.create_task(doc_keyword_extraction(chat_mdl, d, task["parser_config"]["auto_keywords"])))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error in doc_keyword_extraction: {}".format(e))
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        progress_callback(msg="Keywords generation {} chunks completed in {:.2f}s".format(len(docs), timer() - st))

    # 步骤 6：可选 - 使用 LLM 自动为每个 chunk 生成相关问题
    if task["parser_config"].get("auto_questions", 0):
        st = timer()
        progress_callback(msg="Start to generate questions for every chunk ...")
        chat_model_config = get_model_config_by_type_and_name(task["tenant_id"], LLMType.CHAT, task["llm_id"])
        chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])

        async def doc_question_proposal(chat_mdl, d, topn):
            """对单个 chunk 生成相关问题，优先使用 LLM 缓存。"""
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "question", {"topn": topn})
            if not cached:
                if has_canceled(task["id"]):
                    progress_callback(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await question_proposal(chat_mdl, d["content_with_weight"], topn)
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "question", {"topn": topn})
            if cached:
                d["question_kwd"] = cached.split("\n")
                d["question_tks"] = rag_tokenizer.tokenize("\n".join(d["question_kwd"]))

        tasks = []
        for d in docs:
            tasks.append(
                asyncio.create_task(doc_question_proposal(chat_mdl, d, task["parser_config"]["auto_questions"])))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error in doc_question_proposal", exc_info=e)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        progress_callback(msg="Question generation {} chunks completed in {:.2f}s".format(len(docs), timer() - st))

    # 步骤 7：可选 - 使用 LLM 自动为每个 chunk 生成结构化元数据
    if task["parser_config"].get("enable_metadata", False) and task["parser_config"].get("metadata"):
        st = timer()
        progress_callback(msg="Start to generate meta-data for every chunk ...")
        chat_model_config = get_model_config_by_type_and_name(task["tenant_id"], LLMType.CHAT, task["llm_id"])
        chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])

        async def gen_metadata_task(chat_mdl, d):
            """对单个 chunk 生成结构化元数据，优先使用 LLM 缓存。"""
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "metadata",
                                   task["parser_config"]["metadata"])
            if not cached:
                if has_canceled(task["id"]):
                    progress_callback(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await gen_metadata(chat_mdl,
                                                turn2jsonschema(task["parser_config"]["metadata"]),
                                                d["content_with_weight"])
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "metadata",
                              task["parser_config"]["metadata"])
            if cached:
                d["metadata_obj"] = cached

        tasks = []
        for d in docs:
            tasks.append(asyncio.create_task(gen_metadata_task(chat_mdl, d)))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error in doc_question_proposal", exc_info=e)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        # 合并所有 chunk 的元数据并更新到文档级别
        metadata = {}
        for doc in docs:
            metadata = update_metadata_to(metadata, doc["metadata_obj"])
            del doc["metadata_obj"]
        if metadata:
            # 与已有的文档元数据合并
            existing_meta = DocMetadataService.get_document_metadata(task["doc_id"])
            existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
            metadata = update_metadata_to(metadata, existing_meta)
            DocMetadataService.update_document_metadata(task["doc_id"], metadata)
        progress_callback(msg="Question generation {} chunks completed in {:.2f}s".format(len(docs), timer() - st))

    # 步骤 8：可选 - 对 chunk 进行自动标签分类（基于已有知识库中的标签体系）
    if task["kb_parser_config"].get("tag_kb_ids", []):
        progress_callback(msg="Start to tag for every chunk ...")
        kb_ids = task["kb_parser_config"]["tag_kb_ids"]
        tenant_id = task["tenant_id"]
        topn_tags = task["kb_parser_config"].get("topn_tags", 3)
        S = 1000  # 标签检索的数量上限
        st = timer()
        examples = []
        # 从缓存或检索引擎获取标签体系
        all_tags = get_tags_from_cache(kb_ids)
        if not all_tags:
            # 缓存未命中，从检索引擎获取标签
            all_tags = settings.retriever.all_tags_in_portion(tenant_id, kb_ids, S)
            set_tags_to_cache(kb_ids, all_tags)
        else:
            all_tags = json.loads(all_tags)
        chat_model_config = get_model_config_by_type_and_name(tenant_id, LLMType.CHAT, task["llm_id"])
        chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])

        # 分流处理：能通过检索直接匹配标签的 chunk 直接标记，其余用 LLM 分类
        docs_to_tag = []
        for d in docs:
            task_canceled = has_canceled(task["id"])
            if task_canceled:
                progress_callback(-1, msg="Task has been canceled.")
                return None
            if settings.retriever.tag_content(tenant_id, kb_ids, d, all_tags, topn_tags=topn_tags, S=S) and len(
                    d[TAG_FLD]) > 0:
                examples.append({"content": d["content_with_weight"], TAG_FLD: d[TAG_FLD]})
            else:
                docs_to_tag.append(d)

        async def doc_content_tagging(chat_mdl, d, topn_tags):
            """使用 LLM 对单个 chunk 进行标签分类，使用 few-shot 示例提升准确性。"""
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], all_tags, {"topn": topn_tags})
            if not cached:
                if has_canceled(task["id"]):
                    progress_callback(-1, msg="Task has been canceled.")
                    return
                picked_examples = random.choices(examples, k=2) if len(examples) > 2 else examples
                if not picked_examples:
                    picked_examples.append({"content": "This is an example", TAG_FLD: {'example': 1}})
                async with chat_limiter:
                    cached = await content_tagging(
                        chat_mdl,
                        d["content_with_weight"],
                        all_tags,
                        picked_examples,
                        topn_tags,
                    )
                if cached:
                    cached = json.dumps(cached)
            if cached:
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, all_tags, {"topn": topn_tags})
                d[TAG_FLD] = json.loads(cached)

        tasks = []
        for d in docs_to_tag:
            tasks.append(asyncio.create_task(doc_content_tagging(chat_mdl, d, topn_tags)))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error tagging docs: {}".format(e))
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        progress_callback(msg="Tagging {} chunks completed in {:.2f}s".format(len(docs), timer() - st))

    return docs


def build_TOC(task, docs, progress_callback):
    """使用 LLM 从文档 chunk 中自动生成目录（Table of Contents）。

    流程：
    1. 按 page_num 和 top 坐标对 chunk 排序
    2. 调用 LLM 根据内容生成目录结构
    3. 将目录项映射到对应的 chunk ID 范围
    4. 生成一个特殊的 TOC chunk（包含目录 JSON，标记为 toc_kwd）

    Args:
        task: 任务字典
        docs: chunk 列表
        progress_callback: 进度回调函数

    Returns:
        TOC chunk 字典，若目录为空则返回 None
    """
    progress_callback(msg="Start to generate table of content ...")
    chat_model_config = get_model_config_by_type_and_name(task["tenant_id"], LLMType.CHAT, task["llm_id"])
    chat_mdl = LLMBundle(task["tenant_id"], chat_model_config, lang=task["language"])
    docs = sorted(docs, key=lambda d: (
        d.get("page_num_int", 0)[0] if isinstance(d.get("page_num_int", 0), list) else d.get("page_num_int", 0),
        d.get("top_int", 0)[0] if isinstance(d.get("top_int", 0), list) else d.get("top_int", 0)
    ))
    toc: list[dict] = asyncio.run(
        run_toc_from_text([d["content_with_weight"] for d in docs], chat_mdl, progress_callback))
    logging.info("------------ T O C -------------\n" + json.dumps(toc, ensure_ascii=False, indent='  '))
    for ii, item in enumerate(toc):
        try:
            chunk_val = item.pop("chunk_id", None)
            if chunk_val is None or str(chunk_val).strip() == "":
                logging.warning(f"Index {ii}: chunk_id is missing or empty. Skipping.")
                continue
            curr_idx = int(chunk_val)
            if curr_idx >= len(docs):
                logging.error(f"Index {ii}: chunk_id {curr_idx} exceeds docs length {len(docs)}.")
                continue
            item["ids"] = [docs[curr_idx]["id"]]
            if ii + 1 < len(toc):
                next_chunk_val = toc[ii + 1].get("chunk_id", "")
                if str(next_chunk_val).strip() != "":
                    next_idx = int(next_chunk_val)
                    for jj in range(curr_idx + 1, min(next_idx + 1, len(docs))):
                        item["ids"].append(docs[jj]["id"])
                else:
                    logging.warning(f"Index {ii + 1}: next chunk_id is empty, range fill skipped.")
        except (ValueError, TypeError) as e:
            logging.error(f"Index {ii}: Data conversion error - {e}")
        except Exception as e:
            logging.exception(f"Index {ii}: Unexpected error - {e}")

    if toc:
        d = copy.deepcopy(docs[-1])
        d["content_with_weight"] = json.dumps(toc, ensure_ascii=False)
        d["toc_kwd"] = "toc"
        d["available_int"] = 0
        d["page_num_int"] = [100000000]
        d["id"] = xxhash.xxh64(
            (d["content_with_weight"] + str(d["doc_id"])).encode("utf-8", "surrogatepass")).hexdigest()
        return d
    return None


def init_kb(row, vector_size: int):
    """初始化知识库的索引（Elasticsearch 或 Infinity）。

    根据租户 ID 和知识库 ID 创建搜索索引，指定向量维度。

    Args:
        row: 任务/知识库信息字典，需包含 tenant_id 和 kb_id
        vector_size: 向量维度大小

    Returns:
        索引创建结果
    """
    idxnm = search.index_name(row["tenant_id"])
    parser_id = row.get("parser_id", None)
    return settings.docStoreConn.create_idx(idxnm, row.get("kb_id", ""), vector_size, parser_id)


async def embedding(docs, mdl, parser_config=None, callback=None):
    """对 chunk 列表进行向量嵌入（Embedding）。

    嵌入策略：
    - 文件名嵌入：对文档名称生成向量
    - 内容嵌入：对 chunk 文本内容生成向量（优先使用问题文本）
    - 混合加权：filename_embd_weight 控制文件名嵌入与内容嵌入的权重比例

    处理流程：
    1. 准备文本内容（优先使用 question_kwd，否则使用 content_with_weight）
    2. 生成文件名嵌入向量（所有 chunk 共享同一文件名向量）
    3. 分批对内容进行嵌入（受 embed_limiter 并发控制）
    4. 将文件名向量和内容向量加权混合
    5. 将最终向量写入每个 chunk 的 q_<dim>_vec 字段

    Args:
        docs: chunk 列表
        mdl: 嵌入模型（LLMBundle 实例）
        parser_config: 解析器配置，包含 filename_embd_weight 等
        callback: 进度回调函数

    Returns:
        (token_count, vector_size) 元组：消耗的 token 数和向量维度
    """
    if parser_config is None:
        parser_config = {}
    # 准备文本：tts 为文件名列表，cnts 为内容列表（优先使用问题关键词）
    tts, cnts = [], []
    for d in docs:
        tts.append(d.get("docnm_kwd", "Title"))
        c = "\n".join(d.get("question_kwd", []))
        if not c:
            c = d["content_with_weight"]
        # 去除 HTML 表格标签，避免干扰嵌入
        c = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", c)
        if not c:
            c = "None"
        cnts.append(c)

    tk_count = 0
    # 生成文件名嵌入向量（所有 chunk 共享同一文件名向量）
    if len(tts) == len(cnts):
        vts, c = await thread_pool_exec(mdl.encode, tts[0:1])
        tts = np.tile(vts[0], (len(cnts), 1))
        tk_count += c

    @timeout(60)
    def batch_encode(txts):
        """批量编码文本为向量，截断到模型最大长度。"""
        nonlocal mdl
        return mdl.encode([truncate(c, mdl.max_length - 10) for c in txts])

    # 分批进行内容嵌入，受 embed_limiter 并发控制
    cnts_ = np.array([])
    for i in range(0, len(cnts), settings.EMBEDDING_BATCH_SIZE):
        async with embed_limiter:
            vts, c = await thread_pool_exec(batch_encode, cnts[i: i + settings.EMBEDDING_BATCH_SIZE])
        if len(cnts_) == 0:
            cnts_ = vts
        else:
            cnts_ = np.concatenate((cnts_, vts), axis=0)
        tk_count += c
        callback(prog=0.7 + 0.2 * (i + 1) / len(cnts), msg="")
    cnts = cnts_
    # 文件名嵌入权重：控制文件名向量在最终向量中的占比（默认 0.1）
    filename_embd_weight = parser_config.get("filename_embd_weight", 0.1)  # due to the db support none value
    if not filename_embd_weight:
        filename_embd_weight = 0.1
    title_w = float(filename_embd_weight)
    # 加权混合文件名向量和内容向量
    if tts.ndim == 2 and cnts.ndim == 2 and tts.shape == cnts.shape:
        vects = title_w * tts + (1 - title_w) * cnts
    else:
        vects = cnts

    assert len(vects) == len(docs)
    # 将混合向量写入每个 chunk 的 q_<dim>_vec 字段
    vector_size = 0
    for i, d in enumerate(docs):
        v = vects[i].tolist()
        vector_size = len(v)
        d["q_%d_vec" % len(v)] = v
    return tk_count, vector_size


async def run_dataflow(task: dict):
    """执行 Dataflow（数据流/Agent Canvas）类型的任务。

    Dataflow 是用户通过可视化画布编排的文档处理流水线。
    流程：
    1. 从数据库加载 DSL（画布配置）
    2. 构建 Pipeline 并执行，获取输出 chunks
    3. 若 chunks 不含向量，则进行嵌入
    4. 将结果写入文档存储

    Args:
        task: 任务字典，包含 dataflow_id、doc_id 等信息
    """
    from api.db.services.canvas_service import UserCanvasService
    from rag.flow.pipeline import Pipeline

    task_start_ts = timer()
    dataflow_id = task["dataflow_id"]
    doc_id = task["doc_id"]
    task_id = task["id"]
    task_dataset_id = task["kb_id"]

    if task["task_type"] == "dataflow":
        # 正常的 dataflow 任务，从画布服务加载 DSL
        e, cvs = UserCanvasService.get_by_id(dataflow_id)
        assert e, "User pipeline not found."
        dsl = cvs.dsl
    else:
        # 重试的 dataflow 任务，从操作日志中加载 DSL
        e, pipeline_log = PipelineOperationLogService.get_by_id(dataflow_id)
        assert e, "Pipeline log not found."
        dsl = pipeline_log.dsl
        dataflow_id = pipeline_log.pipeline_id
    pipeline = Pipeline(dsl, tenant_id=task["tenant_id"], doc_id=doc_id, task_id=task_id, flow_id=dataflow_id)
    chunks = await pipeline.run(file=task["file"]) if task.get("file") else await pipeline.run()
    if doc_id == CANVAS_DEBUG_DOC_ID:
        return

    if not chunks:
        # Pipeline 未产出 chunks，仅记录操作日志
        PipelineOperationLogService.create(document_id=doc_id, pipeline_id=dataflow_id,
                                           task_type=PipelineTaskType.PARSE, dsl=str(pipeline))
        return

    # 从 Pipeline 输出中提取不同格式的 chunks
    embedding_token_consumption = chunks.get("embedding_token_consumption", 0)
    if chunks.get("chunks"):
        chunks = copy.deepcopy(chunks["chunks"])
    elif chunks.get("json"):
        chunks = copy.deepcopy(chunks["json"])
    elif chunks.get("markdown"):
        chunks = [{"text": [chunks["markdown"]]}]
    elif chunks.get("text"):
        chunks = [{"text": [chunks["text"]]}]
    elif chunks.get("html"):
        chunks = [{"text": [chunks["html"]]}]

    # 检查 chunks 是否已包含向量，若无则需要进行嵌入
    keys = [k for o in chunks for k in list(o.keys())]
    if not any([re.match(r"q_[0-9]+_vec", k) for k in keys]):
        try:
            set_progress(task_id, prog=0.82, msg="\n-------------------------------------\nStart to embedding...")
            e, kb = KnowledgebaseService.get_by_id(task["kb_id"])
            embedding_id = kb.embd_id
            embd_model_config = get_model_config_by_type_and_name(task["tenant_id"], LLMType.EMBEDDING, embedding_id)
            embedding_model = LLMBundle(task["tenant_id"], embd_model_config)

            @timeout(60)
            def batch_encode(txts):
                nonlocal embedding_model
                return embedding_model.encode([truncate(c, embedding_model.max_length - 10) for c in txts])

            vects = np.array([])
            texts = [o.get("questions", o.get("summary", o["text"])) for o in chunks]
            delta = 0.20 / (len(texts) // settings.EMBEDDING_BATCH_SIZE + 1)
            prog = 0.8
            for i in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
                async with embed_limiter:
                    vts, c = await thread_pool_exec(batch_encode, texts[i: i + settings.EMBEDDING_BATCH_SIZE])
                if len(vects) == 0:
                    vects = vts
                else:
                    vects = np.concatenate((vects, vts), axis=0)
                embedding_token_consumption += c
                prog += delta
                if i % (len(texts) // settings.EMBEDDING_BATCH_SIZE / 100 + 1) == 1:
                    set_progress(task_id, prog=prog, msg=f"{i + 1} / {len(texts) // settings.EMBEDDING_BATCH_SIZE}")

            assert len(vects) == len(chunks)
            for i, ck in enumerate(chunks):
                v = vects[i].tolist()
                ck["q_%d_vec" % len(v)] = v
        except TaskCanceledException:
            raise
        except Exception as e:
            set_progress(task_id, prog=-1, msg=f"[ERROR]: {e}")
            PipelineOperationLogService.create(document_id=doc_id, pipeline_id=dataflow_id,
                                               task_type=PipelineTaskType.PARSE, dsl=str(pipeline))
            return

    metadata = {}
    for ck in chunks:
        ck["doc_id"] = doc_id
        ck["kb_id"] = [str(task["kb_id"])]
        ck["docnm_kwd"] = task["name"]
        ck["create_time"] = str(datetime.now()).replace("T", " ")[:19]
        ck["create_timestamp_flt"] = datetime.now().timestamp()
        if not ck.get("id"):
            ck["id"] = xxhash.xxh64((ck["text"] + str(ck["doc_id"])).encode("utf-8")).hexdigest()
        if "questions" in ck:
            if "question_tks" not in ck:
                ck["question_kwd"] = ck["questions"].split("\n")
                ck["question_tks"] = rag_tokenizer.tokenize(str(ck["questions"]))
            del ck["questions"]
        if "keywords" in ck:
            if "important_tks" not in ck:
                ck["important_kwd"] = ck["keywords"].split(",")
                ck["important_tks"] = rag_tokenizer.tokenize(str(ck["keywords"]))
            del ck["keywords"]
        if "summary" in ck:
            if "content_ltks" not in ck:
                ck["content_ltks"] = rag_tokenizer.tokenize(str(ck["summary"]))
                ck["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(ck["content_ltks"])
            del ck["summary"]
        if "metadata" in ck:
            metadata = update_metadata_to(metadata, ck["metadata"])
            del ck["metadata"]
        if "content_with_weight" not in ck:
            ck["content_with_weight"] = ck["text"]
        del ck["text"]
        if "positions" in ck:
            add_positions(ck, ck["positions"])
            del ck["positions"]

    if metadata:
        existing_meta = DocMetadataService.get_document_metadata(doc_id)
        existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
        metadata = update_metadata_to(metadata, existing_meta)
        DocMetadataService.update_document_metadata(doc_id, metadata)

    start_ts = timer()
    set_progress(task_id, prog=0.82, msg="[DOC Engine]:\nStart to index...")
    e = await insert_chunks(task_id, task["tenant_id"], task["kb_id"], chunks, partial(set_progress, task_id, 0, 100000000))
    if not e:
        PipelineOperationLogService.create(document_id=doc_id, pipeline_id=dataflow_id,
                                           task_type=PipelineTaskType.PARSE, dsl=str(pipeline))
        return

    time_cost = timer() - start_ts
    task_time_cost = timer() - task_start_ts
    set_progress(task_id, prog=1., msg="Indexing done ({:.2f}s). Task done ({:.2f}s)".format(time_cost, task_time_cost))
    DocumentService.increment_chunk_num(doc_id, task_dataset_id, embedding_token_consumption, len(chunks),
                                        task_time_cost)
    logging.info("[Done], chunks({}), token({}), elapsed:{:.2f}".format(len(chunks), embedding_token_consumption,
                                                                        task_time_cost))
    PipelineOperationLogService.create(document_id=doc_id, pipeline_id=dataflow_id, task_type=PipelineTaskType.PARSE,
                                       dsl=str(pipeline))


@timeout(3600)
async def run_raptor_for_kb(row, kb_parser_config, chat_mdl, embd_mdl, vector_size, callback=None, doc_ids=[]):
    """执行 RAPTOR（递归抽象构建树状检索）任务。

    RAPTOR 算法流程：
    1. 从文档存储中检索已有 chunk 及其向量
    2. 对 chunks 进行聚类
    3. 使用 LLM 对每个聚类生成摘要
    4. 对摘要进行嵌入，生成新的抽象 chunk
    5. 将抽象 chunk 写入文档存储

    支持两种范围模式：
    - file：按文件维度分别执行 RAPTOR
    - 其他：按知识库维度对所有文件统一执行 RAPTOR

    Args:
        row: 任务/知识库信息字典
        kb_parser_config: 知识库解析器配置，含 raptor 子配置
        chat_mdl: 聊天模型（用于生成摘要）
        embd_mdl: 嵌入模型（用于生成向量）
        vector_size: 向量维度
        callback: 进度回调函数
        doc_ids: 待处理的文档 ID 列表

    Returns:
        (res, tk_count) 元组：生成的抽象 chunk 列表和消耗的 token 数
    """
    fake_doc_id = GRAPH_RAPTOR_FAKE_DOC_ID

    raptor_config = kb_parser_config.get("raptor", {})
    vctr_nm = "q_%d_vec" % vector_size

    res = []
    tk_count = 0
    max_errors = int(os.environ.get("RAPTOR_MAX_ERRORS", 3))
    doc_name_by_id = {}
    for doc_id in set(doc_ids):
        ok, source_doc = DocumentService.get_by_id(doc_id)
        if not ok or not source_doc:
            continue
        source_name = getattr(source_doc, "name", "")
        if source_name:
            doc_name_by_id[doc_id] = source_name

    async def generate(chunks, did):
        """对一组 chunks 执行 RAPTOR 聚类摘要，生成抽象 chunk。

        Args:
            chunks: 输入 chunk 列表，每个为 (content, vector) 元组
            did: 文档 ID（用于关联生成的抽象 chunk）
        """
        nonlocal tk_count, res
        raptor = Raptor(
            raptor_config.get("max_cluster", 64),
            chat_mdl,
            embd_mdl,
            raptor_config["prompt"],
            raptor_config["max_token"],
            raptor_config["threshold"],
            max_errors=max_errors,
        )
        original_length = len(chunks)
        chunks = await raptor(chunks, kb_parser_config["raptor"]["random_seed"], callback, row["id"])
        effective_doc_name = row["name"] if did == fake_doc_id else doc_name_by_id.get(did, row["name"])
        doc = {
            "doc_id": did,
            "kb_id": [str(row["kb_id"])],
            "docnm_kwd": effective_doc_name,
            "title_tks": rag_tokenizer.tokenize(effective_doc_name),
            "raptor_kwd": "raptor"
        }
        if row["pagerank"]:
            doc[PAGERANK_FLD] = int(row["pagerank"])

        for content, vctr in chunks[original_length:]:
            d = copy.deepcopy(doc)
            d["id"] = xxhash.xxh64((content + str(fake_doc_id)).encode("utf-8")).hexdigest()
            d["create_time"] = str(datetime.now()).replace("T", " ")[:19]
            d["create_timestamp_flt"] = datetime.now().timestamp()
            d[vctr_nm] = vctr.tolist()
            d["content_with_weight"] = content
            d["content_ltks"] = rag_tokenizer.tokenize(content)
            d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(d["content_ltks"])
            res.append(d)
            tk_count += num_tokens_from_string(content)

    # 根据范围配置选择执行模式：按文件 or 按知识库
    if raptor_config.get("scope", "file") == "file":
        for x, doc_id in enumerate(doc_ids):
            chunks = []
            skipped_chunks = 0
            for d in settings.retriever.chunk_list(doc_id, row["tenant_id"], [str(row["kb_id"])],
                                                   fields=["content_with_weight", vctr_nm],
                                                   sort_by_position=True):
                # Skip chunks that don't have the required vector field (may have been indexed with different embedding model)
                if vctr_nm not in d or d[vctr_nm] is None:
                    skipped_chunks += 1
                    logging.warning(f"RAPTOR: Chunk missing vector field '{vctr_nm}' in doc {doc_id}, skipping")
                    continue
                chunks.append((d["content_with_weight"], np.array(d[vctr_nm])))
            
            if skipped_chunks > 0:
                callback(msg=f"[WARN] Skipped {skipped_chunks} chunks without vector field '{vctr_nm}' for doc {doc_id}. Consider re-parsing the document with the current embedding model.")
            
            if not chunks:
                logging.warning(f"RAPTOR: No valid chunks with vectors found for doc {doc_id}")
                callback(msg=f"[WARN] No valid chunks with vectors found for doc {doc_id}, skipping")
                continue
                
            await generate(chunks, doc_id)
            callback(prog=(x + 1.) / len(doc_ids))
    else:
        chunks = []
        skipped_chunks = 0
        for doc_id in doc_ids:
            for d in settings.retriever.chunk_list(doc_id, row["tenant_id"], [str(row["kb_id"])],
                                                   fields=["content_with_weight", vctr_nm],
                                                   sort_by_position=True):
                # Skip chunks that don't have the required vector field
                if vctr_nm not in d or d[vctr_nm] is None:
                    skipped_chunks += 1
                    logging.warning(f"RAPTOR: Chunk missing vector field '{vctr_nm}' in doc {doc_id}, skipping")
                    continue
                chunks.append((d["content_with_weight"], np.array(d[vctr_nm])))

        if skipped_chunks > 0:
            callback(msg=f"[WARN] Skipped {skipped_chunks} chunks without vector field '{vctr_nm}'. Consider re-parsing documents with the current embedding model.")

        if not chunks:
            logging.error(f"RAPTOR: No valid chunks with vectors found in any document for kb {row['kb_id']}")
            callback(msg=f"[ERROR] No valid chunks with vectors found. Please ensure documents are parsed with the current embedding model (vector size: {vector_size}).")
            return res, tk_count

        await generate(chunks, fake_doc_id)

    return res, tk_count


async def delete_image(kb_id, chunk_id):
    """从 MinIO 中删除 chunk 对应的图片文件。"""
    try:
        async with minio_limiter:
            settings.STORAGE_IMPL.delete(kb_id, chunk_id)
    except Exception:
        logging.exception(f"Deleting image of chunk {chunk_id} got exception")
        raise


async def insert_chunks(task_id, task_tenant_id, task_dataset_id, chunks, progress_callback):
    """将 chunk 列表批量插入文档存储（Elasticsearch 或 Infinity）。

    处理流程：
    1. 提取"母 chunk"（mom）：包含完整上下文的特殊 chunk，用于层级关联
    2. 先插入母 chunk，再插入普通 chunk
    3. 分批插入，每批 DOC_BULK_SIZE 条
    4. 每批插入后更新 task 的 chunk_ids，用于断点续传
    5. 若任务被取消，回滚已插入的数据

    Args:
        task_id: 任务 ID
        task_tenant_id: 租户 ID
        task_dataset_id: 知识库 ID
        chunks: chunk 字典列表
        progress_callback: 进度回调函数

    Returns:
        True 表示插入成功，False 表示插入被中断
    """
    # 处理母 chunk（mom）：包含完整上下文信息的特殊 chunk
    mothers = []
    mother_ids = set([])
    for ck in chunks:
        mom = ck.get("mom") or ck.get("mom_with_weight") or ""
        if not mom:
            continue
        id = xxhash.xxh64(mom.encode("utf-8")).hexdigest()
        ck["mom_id"] = id
        if id in mother_ids:
            continue
        mother_ids.add(id)
        mom_ck = copy.deepcopy(ck)
        mom_ck["id"] = id
        mom_ck["content_with_weight"] = mom
        mom_ck["available_int"] = 0
        flds = list(mom_ck.keys())
        for fld in flds:
            if fld not in ["id", "content_with_weight", "doc_id", "docnm_kwd", "kb_id", "available_int",
                           "position_int", "create_timestamp_flt", "page_num_int", "top_int"]:
                del mom_ck[fld]
        mothers.append(mom_ck)

    # 先分批插入母 chunk
    for b in range(0, len(mothers), settings.DOC_BULK_SIZE):
        await thread_pool_exec(settings.docStoreConn.insert, mothers[b:b + settings.DOC_BULK_SIZE],
                                search.index_name(task_tenant_id), task_dataset_id, )
        task_canceled = has_canceled(task_id)
        if task_canceled:
            progress_callback(-1, msg="Task has been canceled.")
            return False

    # 分批插入普通 chunk
    for b in range(0, len(chunks), settings.DOC_BULK_SIZE):
        doc_store_result = await thread_pool_exec(settings.docStoreConn.insert, chunks[b:b + settings.DOC_BULK_SIZE],
                                                   search.index_name(task_tenant_id), task_dataset_id, )
        task_canceled = has_canceled(task_id)
        if task_canceled:
            progress_callback(-1, msg="Task has been canceled.")
            return False
        if b % 128 == 0:
            progress_callback(prog=0.8 + 0.1 * (b + 1) / len(chunks), msg="")
        if doc_store_result:
            error_message = f"Insert chunk error: {doc_store_result}, please check log file and Elasticsearch/Infinity status!"
            progress_callback(-1, msg=error_message)
            raise Exception(error_message)
        chunk_ids = [chunk["id"] for chunk in chunks[:b + settings.DOC_BULK_SIZE]]
        chunk_ids_str = " ".join(chunk_ids)
        try:
            TaskService.update_chunk_ids(task_id, chunk_ids_str)
        except DoesNotExist:
            logging.warning(f"do_handle_task update_chunk_ids failed since task {task_id} is unknown.")
            doc_store_result = await thread_pool_exec(settings.docStoreConn.delete, {"id": chunk_ids},
                                                       search.index_name(task_tenant_id), task_dataset_id, )
            tasks = []
            for chunk_id in chunk_ids:
                tasks.append(asyncio.create_task(delete_image(task_dataset_id, chunk_id)))
            try:
                await asyncio.gather(*tasks, return_exceptions=False)
            except Exception as e:
                logging.error(f"delete_image failed: {e}")
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            progress_callback(-1, msg=f"Chunk updates failed since task {task_id} is unknown.")
            return False
    return True


@timeout(60 * 60 * 3, 1)
async def do_handle_task(task):
    """处理单个任务的核心逻辑，是整个任务执行器的核心函数。

    根据任务类型分发到不同的处理路径：
    - memory：调用 handle_save_to_memory_task 保存记忆
    - dataflow：调用 run_dataflow 执行数据流任务
    - raptor：执行 RAPTOR 递归摘要任务
    - graphrag：执行 GraphRAG 知识图谱构建任务
    - mindmap：思维导图任务（占位）
    - 默认：标准文档解析流程（分块 → 嵌入 → 入库）

    标准文档解析流程：
    1. 绑定嵌入模型并验证可用性
    2. 初始化知识库索引
    3. 调用 build_chunks 进行文档分块
    4. 调用 embedding 生成向量
    5. 可选：在后台线程生成 TOC（目录）
    6. 调用 insert_chunks 将结果写入文档存储
    7. 更新文档的 chunk_num 统计

    若任务在执行过程中被取消，会自动清理已写入的数据。

    Args:
        task: 完整的任务字典

    Raises:
        TaskCanceledException: 任务被取消时抛出
    """
    task_type = task.get("task_type", "")

    if task_type == "memory":
        # 记忆保存任务：直接调用记忆服务处理
        await handle_save_to_memory_task(task)
        return

    if task_type == "dataflow" and task.get("doc_id", "") == CANVAS_DEBUG_DOC_ID:
        # Canvas 调试任务：仅运行数据流不入库
        await run_dataflow(task)
        return

    # 提取任务关键参数
    task_id = task["id"]
    task_from_page = task["from_page"]
    task_to_page = task["to_page"]
    task_tenant_id = task["tenant_id"]
    task_embedding_id = task["embd_id"]
    task_language = task["language"]
    # LLM ID 优先级：解析器配置 > 任务配置，知识库级 > 文档级
    doc_task_llm_id = task["parser_config"].get("llm_id") or task["llm_id"]
    kb_task_llm_id = task['kb_parser_config'].get("llm_id") or task["llm_id"]
    task['llm_id'] = kb_task_llm_id
    task_dataset_id = task["kb_id"]
    task_doc_id = task["doc_id"]
    task_document_name = task["name"]
    task_parser_config = task["parser_config"]
    task_start_ts = timer()
    toc_thread = None
    # 用于在后台线程中执行 TOC 生成
    executor = concurrent.futures.ThreadPoolExecutor()

    # 构建进度回调函数，绑定任务 ID 和页码范围
    progress_callback = partial(set_progress, task_id, task_from_page, task_to_page)

    task_canceled = has_canceled(task_id)
    if task_canceled:
        progress_callback(-1, msg="Task has been canceled.")
        return

    try:
        # 步骤 1：绑定嵌入模型，通过编码测试向量验证模型可用性
        if task_embedding_id:
            embd_model_config = get_model_config_by_type_and_name(task_tenant_id, LLMType.EMBEDDING, task_embedding_id)
        else:
            # 使用租户默认嵌入模型
            embd_model_config = get_tenant_default_model_by_type(task_tenant_id, LLMType.EMBEDDING)
        embedding_model = LLMBundle(task_tenant_id, embd_model_config, lang=task_language)
        vts, _ = embedding_model.encode(["ok"])
        vector_size = len(vts[0])
    except Exception as e:
        error_message = f'Fail to bind embedding model: {str(e)}'
        progress_callback(-1, msg=error_message)
        logging.exception(error_message)
        raise

    # 步骤 2：初始化知识库的搜索索引
    init_kb(task, vector_size)

    # 路径 A：Dataflow 任务（非调试模式）
    if task_type[:len("dataflow")] == "dataflow":
        await run_dataflow(task)
        return

    # 路径 B：RAPTOR 递归摘要任务
    if task_type == "raptor":
        ok, kb = KnowledgebaseService.get_by_id(task_dataset_id)
        if not ok:
            progress_callback(prog=-1.0, msg="Cannot found valid dataset for RAPTOR task")
            return

        kb_parser_config = kb.parser_config
        # 若 RAPTOR 配置不存在，使用默认配置并写入数据库
        if not kb_parser_config.get("raptor", {}).get("use_raptor", False):
            kb_parser_config.update(
                {
                    "raptor": {
                        "use_raptor": True,
                        "prompt": "Please summarize the following paragraphs. Be careful with the numbers, do not make things up. Paragraphs as following:\n      {cluster_content}\nThe above is the content you need to summarize.",
                        "max_token": 256,
                        "threshold": 0.1,
                        "max_cluster": 64,
                        "random_seed": 0,
                        "scope": "file"
                    },
                }
            )
            if not KnowledgebaseService.update_by_id(kb.id, {"parser_config": kb_parser_config}):
                progress_callback(prog=-1.0, msg="Internal error: Invalid RAPTOR configuration")
                return

        # Check if Raptor should be skipped for structured data
        file_type = task.get("type", "")
        parser_id = task.get("parser_id", "")
        raptor_config = kb_parser_config.get("raptor", {})

        if should_skip_raptor(file_type, parser_id, task_parser_config, raptor_config):
            skip_reason = get_skip_reason(file_type, parser_id, task_parser_config)
            logging.info(f"Skipping Raptor for document {task_document_name}: {skip_reason}")
            progress_callback(prog=1.0, msg=f"Raptor skipped: {skip_reason}")
            return

        # 绑定 LLM 并执行 RAPTOR，受 kg_limiter 并发控制
        chat_model_config = get_model_config_by_type_and_name(task_tenant_id, LLMType.CHAT, kb_task_llm_id)
        chat_model = LLMBundle(task_tenant_id, chat_model_config, lang=task_language)
        # 执行 RAPTOR，受知识图谱并发信号量限制
        async with kg_limiter:
            chunks, token_count = await run_raptor_for_kb(
                row=task,
                kb_parser_config=kb_parser_config,
                chat_mdl=chat_model,
                embd_mdl=embedding_model,
                vector_size=vector_size,
                callback=progress_callback,
                doc_ids=task.get("doc_ids", []),
            )
        if fake_doc_ids := task.get("doc_ids", []):
            task_doc_id = fake_doc_ids[0]  # use the first document ID to represent this task for logging purposes
    # 路径 C：GraphRAG 知识图谱构建任务
    elif task_type == "graphrag":
        ok, kb = KnowledgebaseService.get_by_id(task_dataset_id)
        if not ok:
            progress_callback(prog=-1.0, msg="Cannot found valid dataset for GraphRAG task")
            return

        kb_parser_config = kb.parser_config
        # 若 GraphRAG 配置不存在，使用默认配置并写入数据库
        if not kb_parser_config.get("graphrag", {}).get("use_graphrag", False):
            kb_parser_config.update(
                {
                    "graphrag": {
                        "use_graphrag": True,
                        "entity_types": [
                            "organization",
                            "person",
                            "geo",
                            "event",
                            "category",
                        ],
                        "method": "light",
                    }
                }
            )
            if not KnowledgebaseService.update_by_id(kb.id, {"parser_config": kb_parser_config}):
                progress_callback(prog=-1.0, msg="Internal error: Invalid GraphRAG configuration")
                return

        graphrag_conf = kb_parser_config.get("graphrag", {})
        start_ts = timer()
        chat_model_config = get_model_config_by_type_and_name(task_tenant_id, LLMType.CHAT, kb_task_llm_id)
        chat_model = LLMBundle(task_tenant_id, chat_model_config, lang=task_language)
        with_resolution = graphrag_conf.get("resolution", False)
        with_community = graphrag_conf.get("community", False)
        async with kg_limiter:
            # await run_graphrag(task, task_language, with_resolution, with_community, chat_model, embedding_model, progress_callback)
            result = await run_graphrag_for_kb(
                row=task,
                doc_ids=task.get("doc_ids", []),
                language=task_language,
                kb_parser_config=kb_parser_config,
                chat_model=chat_model,
                embedding_model=embedding_model,
                callback=progress_callback,
                with_resolution=with_resolution,
                with_community=with_community,
            )
            logging.info(f"GraphRAG task result for task {task}:\n{result}")
        progress_callback(prog=1.0, msg="Knowledge Graph done ({:.2f}s)".format(timer() - start_ts))
        return
    # 路径 D：思维导图任务（占位）
    elif task_type == "mindmap":
        progress_callback(1, "place holder")
        pass
        return
    else:
        # 路径 E：标准文档解析流程（分块 → 嵌入 → 入库）
        task['llm_id'] = doc_task_llm_id
        start_ts = timer()
        # 步骤 3：调用 build_chunks 进行文档分块
        chunks = await build_chunks(task, progress_callback)
        logging.info("Build document {}: {:.2f}s".format(task_document_name, timer() - start_ts))
        if not chunks:
            progress_callback(1., msg=f"No chunk built from {task_document_name}")
            return
        progress_callback(msg="Generate {} chunks".format(len(chunks)))
        start_ts = timer()
        # 步骤 4：对 chunks 进行向量嵌入
        try:
            token_count, vector_size = await embedding(chunks, embedding_model, task_parser_config, progress_callback)
        except TaskCanceledException:
            raise
        except Exception as e:
            error_message = "Generate embedding error:{}".format(str(e))
            progress_callback(-1, error_message)
            logging.exception(error_message)
            token_count = 0
            raise
        progress_message = "Embedding chunks ({:.2f}s)".format(timer() - start_ts)
        logging.info(progress_message)
        progress_callback(msg=progress_message)
        # 步骤 5：若启用 TOC 提取，在后台线程中异步生成目录
        if task["parser_id"].lower() == "naive" and task["parser_config"].get("toc_extraction", False):
            toc_thread = executor.submit(build_TOC, task, chunks, progress_callback)

    chunk_count = len(set([chunk["id"] for chunk in chunks]))
    start_ts = timer()

    # 封装带取消检查的 chunk 插入逻辑
    async def _maybe_insert_chunks(_chunks):
        """检查任务是否取消后执行 chunk 插入操作。"""
        if has_canceled(task_id):
            progress_callback(-1, msg="Task has been canceled.")
            return False
        insert_result = await insert_chunks(task_id, task_tenant_id, task_dataset_id, _chunks, progress_callback)
        return bool(insert_result)

    try:
        # 步骤 6：将 chunks 插入文档存储
        if not await _maybe_insert_chunks(chunks):
            return
        if has_canceled(task_id):
            progress_callback(-1, msg="Task has been canceled.")
            return

        logging.info(
            "Indexing doc({}), page({}-{}), chunks({}), elapsed: {:.2f}".format(
                task_document_name, task_from_page, task_to_page, len(chunks), timer() - start_ts
            )
        )

        # 步骤 7：更新文档的 chunk_num 统计
        DocumentService.increment_chunk_num(task_doc_id, task_dataset_id, token_count, chunk_count, 0)

        progress_callback(msg="Indexing done ({:.2f}s).".format(timer() - start_ts))

        # 等待后台 TOC 线程完成，并将 TOC chunk 插入存储
        if toc_thread:
            d = toc_thread.result()
            if d:
                if not await _maybe_insert_chunks([d]):
                    return
                DocumentService.increment_chunk_num(task_doc_id, task_dataset_id, 0, 1, 0)

        if has_canceled(task_id):
            progress_callback(-1, msg="Task has been canceled.")
            return

        task_time_cost = timer() - task_start_ts
        progress_callback(prog=1.0, msg="Task done ({:.2f}s)".format(task_time_cost))
        logging.info(
            "Chunk doc({}), page({}-{}), chunks({}), token({}), elapsed:{:.2f}".format(
                task_document_name, task_from_page, task_to_page, len(chunks), token_count, task_time_cost
            )
        )

    finally:
        # 若任务被取消，清理已写入文档存储的数据，避免残留脏数据
        if has_canceled(task_id):
            try:
                exists = await thread_pool_exec(
                    settings.docStoreConn.index_exist,
                    search.index_name(task_tenant_id),
                    task_dataset_id,
                )
                if exists:
                    await thread_pool_exec(
                        settings.docStoreConn.delete,
                        {"doc_id": task_doc_id},
                        search.index_name(task_tenant_id),
                        task_dataset_id,
                    )
            except Exception as e:
                logging.exception(
                    f"Remove doc({task_doc_id}) from docStore failed when task({task_id}) canceled, exception: {e}")


async def handle_task():
    """从队列获取并执行一个任务，处理完成或失败后确认消息。

    任务执行结果分类：
    - 成功或取消：增加 DONE_TASKS 计数
    - 异常：增加 FAILED_TASKS 计数，并设置错误进度消息
    最终记录流水线操作日志。
    """
    global DONE_TASKS, FAILED_TASKS
    redis_msg, task = await collect()
    if not task:
        # 无任务可用，等待 5 秒后重试（避免 CPU 空转）
        await asyncio.sleep(5)
        return

    task_type = task["task_type"]
    pipeline_task_type = TASK_TYPE_TO_PIPELINE_TASK_TYPE.get(task_type,
                                                             PipelineTaskType.PARSE) or PipelineTaskType.PARSE
    task_id = task["id"]
    try:
        logging.info(f"handle_task begin for task {json.dumps(task)}")
        CURRENT_TASKS[task["id"]] = copy.deepcopy(task)
        await do_handle_task(task)
        DONE_TASKS += 1
        CURRENT_TASKS.pop(task_id, None)
        logging.info(f"handle_task done for task {json.dumps(task)}")
    except TaskCanceledException as e:
        DONE_TASKS += 1
        CURRENT_TASKS.pop(task_id, None)
        logging.info(
            f"handle_task canceled for task {task_id}: {getattr(e, 'msg', str(e))}"
        )
    except Exception as e:
        FAILED_TASKS += 1
        CURRENT_TASKS.pop(task_id, None)
        try:
            err_msg = str(e)
            while isinstance(e, exceptiongroup.ExceptionGroup):
                e = e.exceptions[0]
                err_msg += ' -- ' + str(e)
            set_progress(task_id, prog=-1, msg=f"[Exception]: {err_msg}")
        except Exception as e:
            logging.exception(f"[Exception]: {str(e)}")
            pass
        logging.exception(f"handle_task got exception for task {json.dumps(task)}")
    finally:
        # 记录流水线操作日志（用于审计和调试）
        task_document_ids = []
        if task_type in ["graphrag", "raptor", "mindmap"]:
            task_document_ids = task["doc_ids"]
        # 非 dataflow 任务记录操作日志
        if not task.get("dataflow_id", ""):
            PipelineOperationLogService.record_pipeline_operation(document_id=task["doc_id"], pipeline_id="",
                                                                  task_type=pipeline_task_type,
                                                                  fake_document_ids=task_document_ids)

    # 确认消息（ACK），标记任务已处理
    redis_msg.ack()


async def get_server_ip() -> str:
    """通过 UDP 连接获取本机 IP 地址（不实际发送数据）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception as e:
        logging.error(str(e))
        return 'Unknown'


async def report_status():
    """定期向 Redis 上报 Worker 心跳状态。

    每 30 秒执行一次，完成以下操作：
    1. 注册当前 Worker 到 TASKEXE 集合
    2. 收集队列 pending/lag 统计信息
    3. 将心跳数据写入 Redis Sorted Set（以时间戳为 score）
    4. 清理自身 30 分钟前的过期心跳
    5. 获取分布式锁后，清理其他已超时的 Worker
    """
    global PENDING_TASKS, LAG_TASKS, DONE_TASKS, FAILED_TASKS

    ip_address = await get_server_ip()
    pid = os.getpid()

    # Register the executor in Redis
    # 将当前 Worker 注册到 TASKEXE 集合
    REDIS_CONN.sadd("TASKEXE", CONSUMER_NAME)
    # 用于清理其他超时 Worker 的分布式锁（60 秒超时）
    redis_lock = RedisDistributedLock("clean_task_executor", lock_value=CONSUMER_NAME, timeout=60)

    while True:
        now = datetime.now()
        now_ts = now.timestamp()

        # 获取 Redis Stream 的 pending/lag 统计
        group_info = REDIS_CONN.queue_info(settings.get_svr_queue_name(0), SVR_CONSUMER_GROUP_NAME) or {}
        PENDING_TASKS = int(group_info.get("pending", 0))
        LAG_TASKS = int(group_info.get("lag", 0))

        current = copy.deepcopy(CURRENT_TASKS)
        heartbeat = json.dumps({
            "ip_address": ip_address,
            "pid": pid,
            "name": CONSUMER_NAME,
            "now": now.astimezone().isoformat(timespec="milliseconds"),
            "boot_at": BOOT_AT,
            "pending": PENDING_TASKS,
            "lag": LAG_TASKS,
            "done": DONE_TASKS,
            "failed": FAILED_TASKS,
            "current": current,
        })

        # Report heartbeat to Redis
        try:
            REDIS_CONN.zadd(CONSUMER_NAME, heartbeat, now_ts)
        except Exception as e:
            logging.warning(f"Failed to report heartbeat: {e}")
        else:
            logging.info(f"{CONSUMER_NAME} reported heartbeat: {heartbeat}")

        # Clean up own expired heartbeat
        try:
            REDIS_CONN.zremrangebyscore(CONSUMER_NAME, 0, now_ts - 60 * 30)
        except Exception as e:
            logging.warning(f"Failed to clean heartbeat: {e}")

        # 清理其他已超时的 Worker（需获取分布式锁，避免多 Worker 同时清理）
        lock_acquired = False
        try:
            lock_acquired = redis_lock.acquire()
        except Exception as e:
            logging.warning(f"Failed to acquire Redis lock: {e}")
        if lock_acquired:
            try:
                task_executors = REDIS_CONN.smembers("TASKEXE") or set()
                for worker_name in task_executors:
                    if worker_name == CONSUMER_NAME:
                        continue
                    try:
                        last_heartbeat = REDIS_CONN.REDIS.zrevrange(worker_name, 0, 0, withscores=True)
                    except Exception as e:
                        logging.warning(f"Failed to read zset for {worker_name}: {e}")
                        continue

                    # 检查心跳是否超时，超时则移除该 Worker
                    if not last_heartbeat or now_ts - last_heartbeat[0][1] > WORKER_HEARTBEAT_TIMEOUT:
                        logging.info(f"{worker_name} expired, removed")
                        REDIS_CONN.srem("TASKEXE", worker_name)
                        REDIS_CONN.delete(worker_name)
            except Exception as e:
                logging.warning(f"Failed to clean other executors: {e}")
            finally:
                redis_lock.release()
        await asyncio.sleep(30)


async def task_manager():
    """任务管理器：执行单个任务后释放并发信号量。

    配合 main() 中的 task_limiter 实现最大并发任务数控制。
    """
    try:
        await handle_task()
    finally:
        task_limiter.release()


async def main():
    """任务执行器主入口函数。

    启动流程：
    1. 交错启动（根据 Worker 编号延迟，避免连接风暴）
    2. 初始化日志、配置、设置
    3. 注册信号处理器
    4. 启动心跳上报协程
    5. 进入主循环：获取并发信号量 → 创建任务 → 重复

    主循环通过 task_limiter 控制最大并发任务数，
    每个任务由 task_manager 协程管理生命周期。
    """
    # Stagger executor startup to prevent connection storm to Infinity
    # Extract worker number from CONSUMER_NAME (e.g., "task_executor_abc123_5" -> 5)
    try:
        worker_num = int(CONSUMER_NAME.rsplit("_", 1)[-1])
        # Add random delay: base delay + worker_num * 2.0s + random jitter
        # This spreads out connection attempts over several seconds
        startup_delay = worker_num * 2.0 + random.uniform(0, 0.5)
        if startup_delay > 0:
            logging.info(f"Staggering startup by {startup_delay:.2f}s to prevent connection storm")
            await asyncio.sleep(startup_delay)
    except (ValueError, IndexError):
        pass  # Non-standard consumer name, skip delay

    logging.info(r"""
    ____                      __  _
   /  _/___  ____ ____  _____/ /_(_)___  ____     ________  ______   _____  _____
   / // __ \/ __ `/ _ \/ ___/ __/ / __ \/ __ \   / ___/ _ \/ ___/ | / / _ \/ ___/
 _/ // / / / /_/ /  __(__  ) /_/ / /_/ / / / /  (__  )  __/ /   | |/ /  __/ /
/___/_/ /_/\__, /\___/____/\__/_/\____/_/ /_/  /____/\___/_/    |___/\___/_/
          /____/
    """)
    logging.info(f'RAGFlow ingestion version: {get_ragflow_version()}')
    show_configs()
    settings.init_settings()
    settings.check_and_install_torch()
    logging.info(f'default embedding config: {settings.EMBEDDING_CFG}')
    settings.print_rag_settings()
    if sys.platform != "win32":
        signal.signal(signal.SIGUSR1, start_tracemalloc_and_snapshot)
        signal.signal(signal.SIGUSR2, stop_tracemalloc)
    TRACE_MALLOC_ENABLED = int(os.environ.get('TRACE_MALLOC_ENABLED', "0"))
    if TRACE_MALLOC_ENABLED:
        start_tracemalloc_and_snapshot(None, None)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动心跳上报协程
    report_task = asyncio.create_task(report_status())
    tasks = []

    logging.info(f"RAGFlow ingestion is ready after {time.time() - start_ts}s initialization.")
    try:
        # 主循环：持续获取并发信号量并创建新任务
        while not stop_event.is_set():
            await task_limiter.acquire()
            t = asyncio.create_task(task_manager())
            tasks.append(t)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        report_task.cancel()
        await asyncio.gather(report_task, return_exceptions=True)
    logging.error("BUG!!! You should not reach here!!!")


if __name__ == "__main__":
    faulthandler.enable()
    init_root_logger(CONSUMER_NAME)
    try:
        asyncio.run(main())
    except Exception as e:
        logging.exception(f"Unhandled exception: {e}")
        sys.exit(1)
