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
"""
Memory RESTful API 模块

本模块提供记忆（Memory）相关的 RESTful API 接口，包括：
- 创建记忆库
- 更新记忆库配置
- 删除记忆库
- 查询记忆库列表和配置
- 添加消息到记忆库
- 遗忘（删除）消息
- 更新消息状态
- 搜索消息
- 获取消息内容

主要功能：
- 记忆库的 CRUD 操作
- 消息的增删改查
- 基于语义相似度的消息检索
- 支持多维度过滤（memory_id、agent_id、session_id、user_id）
"""
import logging
import os
import time

from quart import request
from common.constants import RetCode
from common.exceptions import ArgumentException, NotFoundException
from api.apps import login_required, current_user
from api.utils.api_utils import validate_request, get_request_json, get_error_argument_result, get_json_result
from api.apps.services import memory_api_service
from api.utils.tenant_utils import ensure_tenant_model_id_for_params


@manager.route("/memories", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name", "memory_type", "embd_id", "llm_id")
async def create_memory():
    """
    创建记忆库 (POST /memories)。

    创建一个新的记忆库，用于存储和检索对话历史消息。
    需要指定记忆库的名称、类型、嵌入模型和 LLM 模型。

    Args:
        name: 记忆库名称
        memory_type: 记忆库类型
        embd_id: 嵌入模型 ID
        llm_id: LLM 模型 ID

    Returns:
        返回创建的记忆库信息
    """
    timing_enabled = os.getenv("RAGFLOW_API_TIMING")
    t_start = time.perf_counter() if timing_enabled else None
    req = await get_request_json()
    req = ensure_tenant_model_id_for_params(current_user.id, req)
    t_parsed = time.perf_counter() if timing_enabled else None
    try:
        memory_info = {
            "name": req["name"],
            "memory_type": req["memory_type"],
            "embd_id": req["embd_id"],
            "llm_id": req["llm_id"],
            "tenant_embd_id": req["tenant_embd_id"],
            "tenant_llm_id": req["tenant_llm_id"],
        }
        success, res = await memory_api_service.create_memory(memory_info)
        if timing_enabled:
            logging.info(
                "api_timing create_memory parse_ms=%.2f validate_and_db_ms=%.2f total_ms=%.2f path=%s",
                (t_parsed - t_start) * 1000,
                (time.perf_counter() - t_parsed) * 1000,
                (time.perf_counter() - t_start) * 1000,
                request.path,
            )
        if success:
            return get_json_result(message=True, data=res)
        else:
            return get_json_result(message=res, code=RetCode.SERVER_ERROR)

    except ArgumentException as arg_error:
        logging.error(arg_error)
        if timing_enabled:
            logging.info(
                "api_timing create_memory error=%s parse_ms=%.2f total_ms=%.2f path=%s",
                str(arg_error),
                (t_parsed - t_start) * 1000,
                (time.perf_counter() - t_start) * 1000,
                request.path,
            )
        return get_error_argument_result(str(arg_error))

    except Exception as e:
        logging.error(e)
        if timing_enabled:
            logging.info(
                "api_timing create_memory error=%s parse_ms=%.2f total_ms=%.2f path=%s",
                str(e),
                (t_parsed - t_start) * 1000,
                (time.perf_counter() - t_start) * 1000,
                request.path,
            )
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/memories/<memory_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_memory(memory_id):
    """
    更新记忆库配置 (PUT /memories/<memory_id>)。

    更新指定记忆库的配置信息，包括名称、权限、模型设置等。

    Args:
        memory_id: 记忆库 ID

    Returns:
        返回更新后的记忆库信息
    """
    req = await get_request_json()
    new_settings = {k: req[k] for k in [
        "name", "permissions", "llm_id", "embd_id", "memory_type", "memory_size", "forgetting_policy", "temperature",
        "avatar", "description", "system_prompt", "user_prompt", "tenant_llm_id", "tenant_embd_id"
    ] if k in req}
    try:
        success, res = await memory_api_service.update_memory(memory_id, new_settings)
        if success:
            return get_json_result(message=True, data=res)
        else:
            return get_json_result(message=res, code=RetCode.SERVER_ERROR)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except ArgumentException as arg_error:
        logging.error(arg_error)
        return get_error_argument_result(str(arg_error))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/memories/<memory_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_memory(memory_id):
    """
    删除记忆库 (DELETE /memories/<memory_id>)。

    删除指定的记忆库及其所有存储的消息。

    Args:
        memory_id: 记忆库 ID

    Returns:
        返回删除操作的结果
    """
    try:
        await memory_api_service.delete_memory(memory_id)
        return get_json_result(message=True)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/memories", methods=["GET"])  # noqa: F821
@login_required
async def list_memory():
    """
    查询记忆库列表 (GET /memories)。

    根据过滤条件和关键词查询记忆库列表，支持分页。

    Args:
        memory_type: 记忆库类型过滤（可选）
        tenant_id: 租户 ID 过滤（可选）
        storage_type: 存储类型过滤（可选）
        keywords: 搜索关键词（可选）
        page: 页码（可选，默认 1）
        page_size: 每页数量（可选，默认 50）

    Returns:
        返回记忆库列表和分页信息
    """
    filter_params = {
        k: request.args.get(k) for k in ["memory_type", "tenant_id", "storage_type"] if k in request.args
    }
    keywords = request.args.get("keywords")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 50))
    try:
        res = await memory_api_service.list_memory(filter_params, keywords, page, page_size)
        return get_json_result(message=True, data=res)
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/memories/<memory_id>/config", methods=["GET"])  # noqa: F821
@login_required
async def get_memory_config(memory_id):
    """
    获取记忆库配置 (GET /memories/<memory_id>/config)。

    查询指定记忆库的详细配置信息。

    Args:
        memory_id: 记忆库 ID

    Returns:
        返回记忆库的配置信息
    """
    try:
        res = await memory_api_service.get_memory_config(memory_id)
        return get_json_result(message=True, data=res)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/memories/<memory_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_memory_messages(memory_id):
    """
    获取记忆库中的消息列表 (GET /memories/<memory_id>)。

    查询指定记忆库中的消息历史，支持按 agent_id 和关键词过滤，支持分页。

    Args:
        memory_id: 记忆库 ID
        agent_id: Agent ID 过滤（可选，支持多个）
        keywords: 搜索关键词（可选）
        page: 页码（可选，默认 1）
        page_size: 每页数量（可选，默认 50）

    Returns:
        返回消息列表和分页信息
    """
    args = request.args
    agent_ids = args.getlist("agent_id")
    if len(agent_ids) == 1 and ',' in agent_ids[0]:
        agent_ids = agent_ids[0].split(',')
    keywords = args.get("keywords", "")
    keywords = keywords.strip()
    page = int(args.get("page", 1))
    page_size = int(args.get("page_size", 50))
    try:
        res = await memory_api_service.get_memory_messages(
            memory_id, agent_ids, keywords, page, page_size
        )
        return get_json_result(message=True, data=res)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/messages", methods=["POST"]) # noqa: F821
@login_required
@validate_request("memory_id", "agent_id", "session_id", "user_input", "agent_response")
async def add_message():
    """
    添加消息到记忆库 (POST /messages)。

    将对话消息（用户输入和 Agent 响应）添加到指定的记忆库中。

    Args:
        memory_id: 记忆库 ID 列表
        agent_id: Agent ID
        session_id: 会话 ID
        user_input: 用户输入内容
        agent_response: Agent 响应内容

    Returns:
        返回添加操作的结果
    """
    req = await get_request_json()
    memory_ids = req["memory_id"]

    message_dict = {
        "user_id": req.get("user_id"),
        "agent_id": req["agent_id"],
        "session_id": req["session_id"],
        "user_input": req["user_input"],
        "agent_response": req["agent_response"],
    }

    res, msg = await memory_api_service.add_message(memory_ids, message_dict)
    if res:
        return get_json_result(message=msg)

    return get_json_result(message="Some messages failed to add. Detail:" + msg, code=RetCode.SERVER_ERROR)


@manager.route("/messages/<memory_id>:<message_id>", methods=["DELETE"]) # noqa: F821
@login_required
async def forget_message(memory_id: str, message_id: int):
    """
    遗忘（删除）消息 (DELETE /messages/<memory_id>:<message_id>)。

    从记忆库中删除指定的消息记录。

    Args:
        memory_id: 记忆库 ID
        message_id: 消息 ID

    Returns:
        返回删除操作的结果
    """
    try:
        res = await memory_api_service.forget_message(memory_id, message_id)
        return get_json_result(message=res)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/messages/<memory_id>:<message_id>", methods=["PUT"]) # noqa: F821
@login_required
@validate_request("status")
async def update_message(memory_id: str, message_id: int):
    """
    更新消息状态 (PUT /messages/<memory_id>:<message_id>)。

    更新指定消息的状态标志。

    Args:
        memory_id: 记忆库 ID
        message_id: 消息 ID
        status: 状态值（布尔值）

    Returns:
        返回更新操作的结果
    """
    req = await get_request_json()
    status = req["status"]
    if not isinstance(status, bool):
        return get_error_argument_result("Status must be a boolean.")

    try:
        update_succeed = await memory_api_service.update_message_status(memory_id, message_id, status)
        if update_succeed:
            return get_json_result(message=update_succeed)
        else:
            return get_json_result(code=RetCode.SERVER_ERROR, message=f"Failed to set status for message '{message_id}' in memory '{memory_id}'.")
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/messages/search", methods=["GET"]) # noqa: F821
@login_required
async def search_message():
    """
    搜索消息 (GET /messages/search)。

    基于语义相似度在记忆库中搜索相关消息。
    支持向量相似度和关键词相似度的加权组合。

    Args:
        memory_id: 记忆库 ID 列表（支持多个）
        query: 搜索查询文本
        similarity_threshold: 相似度阈值（可选，默认 0.2）
        keywords_similarity_weight: 关键词相似度权重（可选，默认 0.7）
        top_n: 返回结果数量（可选，默认 5）
        agent_id: Agent ID 过滤（可选）
        session_id: 会话 ID 过滤（可选）
        user_id: 用户 ID 过滤（可选）

    Returns:
        返回搜索结果列表
    """
    args = request.args
    memory_ids = args.getlist("memory_id")
    if len(memory_ids) == 1 and ',' in memory_ids[0]:
        memory_ids = memory_ids[0].split(',')
    query = args.get("query")
    similarity_threshold = float(args.get("similarity_threshold", 0.2))
    keywords_similarity_weight = float(args.get("keywords_similarity_weight", 0.7))
    top_n = int(args.get("top_n", 5))
    agent_id = args.get("agent_id", "")
    session_id = args.get("session_id", "")
    user_id = args.get("user_id", "")

    filter_dict = {
        "memory_id": memory_ids,
        "agent_id": agent_id,
        "session_id": session_id,
        "user_id": user_id
    }
    params = {
        "query": query,
        "similarity_threshold": similarity_threshold,
        "keywords_similarity_weight": keywords_similarity_weight,
        "top_n": top_n
    }
    res = await memory_api_service.search_message(filter_dict, params)
    return get_json_result(message=True, data=res)

@manager.route("/messages", methods=["GET"]) # noqa: F821
@login_required
async def get_messages():
    """
    获取消息列表 (GET /messages)。

    根据过滤条件查询消息列表，支持按 memory_id、agent_id、session_id 过滤。

    Args:
        memory_id: 记忆库 ID 列表（必填）
        agent_id: Agent ID 过滤（可选）
        session_id: 会话 ID 过滤（可选）
        limit: 返回结果数量限制（可选，默认 10）

    Returns:
        返回消息列表
    """
    args = request.args
    memory_ids = args.getlist("memory_id")
    if len(memory_ids) == 1 and ',' in memory_ids[0]:
        memory_ids = memory_ids[0].split(',')
    agent_id = args.get("agent_id", "")
    session_id = args.get("session_id", "")
    limit = int(args.get("limit", 10))
    if not memory_ids:
        return get_error_argument_result("memory_ids is required.")
    try:
        res = await memory_api_service.get_messages(memory_ids, agent_id, session_id, limit)
        return get_json_result(message=True, data=res)
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")


@manager.route("/messages/<memory_id>:<message_id>/content", methods=["GET"]) # noqa: F821
@login_required
async def get_message_content(memory_id: str, message_id: int):
    """
    获取消息内容 (GET /messages/<memory_id>:<message_id>/content)。

    查询指定消息的详细内容。

    Args:
        memory_id: 记忆库 ID
        message_id: 消息 ID

    Returns:
        返回消息的详细内容
    """
    try:
        res = await memory_api_service.get_message_content(memory_id, message_id)
        return get_json_result(message=True, data=res)
    except NotFoundException as not_found_exception:
        logging.error(not_found_exception)
        return get_json_result(code=RetCode.NOT_FOUND, message=str(not_found_exception))
    except Exception as e:
        logging.error(e)
        return get_json_result(code=RetCode.SERVER_ERROR, message="Internal server error")
