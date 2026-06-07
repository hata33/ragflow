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
"""记忆（Memory）API 服务模块

该模块提供了记忆管理的核心功能，包括记忆的创建、更新、删除、查询，
以及消息的管理、搜索和遗忘等功能。
"""

from api.apps import current_user
from api.db import TenantPermission
from api.db.services.memory_service import MemoryService
from api.db.services.user_service import UserTenantService
from api.db.services.canvas_service import UserCanvasService
from api.db.services.task_service import TaskService
from api.db.joint_services.memory_message_service import get_memory_size_cache, judge_system_prompt_is_default, queue_save_to_memory_task, query_message
from api.utils.memory_utils import format_ret_data_from_memory, get_memory_type_human
from api.constants import MEMORY_NAME_LIMIT, MEMORY_SIZE_LIMIT
from memory.services.messages import MessageService
from memory.utils.prompt_util import PromptAssembler
from common.constants import MemoryType, ForgettingPolicy
from common.exceptions import ArgumentException, NotFoundException
from common.time_utils import current_timestamp, timestamp_to_date


def _split_filter_values(values):
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    res = []
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            res.extend([v.strip() for v in value.split(",") if v.strip()])
        else:
            res.append(value)
    return res


def _joined_tenant_ids(user_id: str) -> set[str]:
    user_tenants = UserTenantService.get_user_tenant_relation_by_user_id(user_id)
    return {user_id, *[tenant["tenant_id"] for tenant in user_tenants]}


def _memory_accessible(memory) -> bool:
    if memory.tenant_id == current_user.id:
        return True
    if memory.permissions != TenantPermission.TEAM.value:
        return False
    return memory.tenant_id in _joined_tenant_ids(current_user.id)


def _require_memory_access(memory_id: str):
    memory = MemoryService.get_by_memory_id(memory_id)
    if not memory or not _memory_accessible(memory):
        raise NotFoundException(f"Memory '{memory_id}' not found.")
    return memory


def _filter_accessible_memories(memory_ids: list[str]):
    memory_ids = _split_filter_values(memory_ids)
    if not memory_ids:
        return []
    return [memory for memory in MemoryService.get_by_ids(memory_ids) if _memory_accessible(memory)]


async def create_memory(memory_info: dict):
    """创建新记忆

    Args:
        memory_info: 记忆信息字典，包含：
            - name: 记忆名称
            - memory_type: 记忆类型列表
            - embd_id: 嵌入模型 ID
            - llm_id: LLM 模型 ID
            - tenant_embd_id: 租户默认嵌入模型 ID
            - tenant_llm_id: 租户默认 LLM 模型 ID

    Returns:
        (成功标志, 记忆信息) 或 (成功标志, 错误信息)
    """
    # check name length
    name = memory_info["name"]
    memory_name = name.strip()
    if len(memory_name) == 0:
        raise ArgumentException("Memory name cannot be empty or whitespace.")
    if len(memory_name) > MEMORY_NAME_LIMIT:
        raise ArgumentException(f"Memory name '{memory_name}' exceeds limit of {MEMORY_NAME_LIMIT}.")
    # check memory_type valid
    if not isinstance(memory_info["memory_type"], list):
        raise ArgumentException("Memory type must be a list.")
    memory_type = set(memory_info["memory_type"])
    invalid_type = memory_type - {e.name.lower() for e in MemoryType}
    if invalid_type:
        raise ArgumentException(f"Memory type '{invalid_type}' is not supported.")
    memory_type = list(memory_type)
    success, res = MemoryService.create_memory(
        tenant_id=current_user.id,
        name=memory_name,
        memory_type=memory_type,
        embd_id=memory_info["embd_id"],
        llm_id=memory_info["llm_id"]
    )
    if success:
        return True, format_ret_data_from_memory(res)
    else:
        return False, res


async def update_memory(memory_id: str, new_memory_setting: dict):
    """更新记忆设置

    Args:
        memory_id: 记忆 ID
        new_memory_setting: 新的记忆设置，包含：
            - name: 记忆名称
            - permissions: 权限
            - llm_id: LLM 模型 ID
            - embd_id: 嵌入模型 ID
            - memory_type: 记忆类型列表
            - memory_size: 记忆大小
            - forgetting_policy: 遗忘策略
            - temperature: 温度参数
            - avatar: 头像
            - description: 描述
            - system_prompt: 系统提示词
            - user_prompt: 用户提示词

    Returns:
        (成功标志, 记忆信息) 或 (成功标志, 错误信息)
    """
    current_memory = _require_memory_access(memory_id)

    update_dict = {}
    # check name length
    if "name" in new_memory_setting:
        name = new_memory_setting["name"]
        memory_name = name.strip()
        if len(memory_name) == 0:
            raise ArgumentException("Memory name cannot be empty or whitespace.")
        if len(memory_name) > MEMORY_NAME_LIMIT:
            raise ArgumentException(f"Memory name '{memory_name}' exceeds limit of {MEMORY_NAME_LIMIT}.")
        update_dict["name"] = memory_name
    # check permissions valid
    if new_memory_setting.get("permissions"):
        if new_memory_setting["permissions"] not in [e.value for e in TenantPermission]:
            raise ArgumentException(f"Unknown permission '{new_memory_setting['permissions']}'.")
        update_dict["permissions"] = new_memory_setting["permissions"]
    if new_memory_setting.get("llm_id") or new_memory_setting.get("embd_id"):
        merged = {
            "llm_id": new_memory_setting.get("llm_id") or current_memory.llm_id,
            "embd_id": new_memory_setting.get("embd_id") or current_memory.embd_id,
        }
        if new_memory_setting.get("llm_id"):
            update_dict["llm_id"] = merged["llm_id"]
        if new_memory_setting.get("embd_id"):
            update_dict["embd_id"] = merged["embd_id"]
    if new_memory_setting.get("memory_type"):
        memory_type = set(new_memory_setting["memory_type"])
        invalid_type = memory_type - {e.name.lower() for e in MemoryType}
        if invalid_type:
            raise ArgumentException(f"Memory type '{invalid_type}' is not supported.")
        update_dict["memory_type"] = list(memory_type)
    # check memory_size valid
    if new_memory_setting.get("memory_size"):
        if not 0 < int(new_memory_setting["memory_size"]) <= MEMORY_SIZE_LIMIT:
            raise ArgumentException(f"Memory size should be in range (0, {MEMORY_SIZE_LIMIT}] Bytes.")
        update_dict["memory_size"] = new_memory_setting["memory_size"]
    # check forgetting_policy valid
    if new_memory_setting.get("forgetting_policy"):
        if new_memory_setting["forgetting_policy"] not in [e.value for e in ForgettingPolicy]:
            raise ArgumentException(f"Forgetting policy '{new_memory_setting['forgetting_policy']}' is not supported.")
        update_dict["forgetting_policy"] = new_memory_setting["forgetting_policy"]
    # check temperature valid
    if "temperature" in new_memory_setting:
        temperature = float(new_memory_setting["temperature"])
        if not 0 <= temperature <= 1:
            raise ArgumentException("Temperature should be in range [0, 1].")
        update_dict["temperature"] = temperature
    # allow update to empty fields
    for field in ["avatar", "description", "system_prompt", "user_prompt"]:
        if field in new_memory_setting:
            update_dict[field] = new_memory_setting[field]

    memory_dict = current_memory.to_dict()
    memory_dict.update({"memory_type": get_memory_type_human(current_memory.memory_type)})
    to_update = {}
    for k, v in update_dict.items():
        if isinstance(v, list) and set(memory_dict[k]) != set(v):
            to_update[k] = v
        elif memory_dict[k] != v:
            to_update[k] = v

    if not to_update:
        return True, memory_dict
    # check memory empty when update embd_id, memory_type
    memory_size = get_memory_size_cache(memory_id, current_memory.tenant_id)
    not_allowed_update = [f for f in ["embd_id", "memory_type"] if f in to_update and memory_size > 0]
    if not_allowed_update:
        raise ArgumentException(f"Can't update {not_allowed_update} when memory isn't empty.")
    if "memory_type" in to_update:
        if "system_prompt" not in to_update and judge_system_prompt_is_default(current_memory.system_prompt, current_memory.memory_type):
            # update old default prompt, assemble a new one
            to_update["system_prompt"] = PromptAssembler.assemble_system_prompt({"memory_type": to_update["memory_type"]})

    MemoryService.update_memory(current_memory.tenant_id, memory_id, to_update)
    updated_memory = MemoryService.get_by_memory_id(memory_id)
    return True, format_ret_data_from_memory(updated_memory)


async def delete_memory(memory_id):
    memory = _require_memory_access(memory_id)
    MemoryService.delete_memory(memory_id)
    if MessageService.has_index(memory.tenant_id, memory_id):
        MessageService.delete_message({"memory_id": memory_id}, memory.tenant_id, memory_id)
    return True


async def list_memory(filter_params: dict, keywords: str, page: int=1, page_size: int = 50):
    """列出记忆

    Args:
        filter_params: 过滤参数，包含：
            - memory_type: 记忆类型列表
            - tenant_id: 租户 ID 列表
            - storage_type: 存储类型
        keywords: 关键词
        page: 页码
        page_size: 每页大小

    Returns:
        包含记忆列表和总数的字典
    """
    filter_dict: dict = {"storage_type": filter_params.get("storage_type"), "accessible_user_id": current_user.id}
    allowed_tenant_ids = _joined_tenant_ids(current_user.id)
    tenant_ids = _split_filter_values(filter_params.get("tenant_id") or filter_params.get("owner_ids"))
    if tenant_ids:
        filter_dict["tenant_id"] = [tenant_id for tenant_id in tenant_ids if tenant_id in allowed_tenant_ids]
        if not filter_dict["tenant_id"]:
            return {"memory_list": [], "total_count": 0}
    else:
        filter_dict["tenant_id"] = list(allowed_tenant_ids)
    memory_types = _split_filter_values(filter_params.get("memory_type"))
    filter_dict["memory_type"] = memory_types

    memory_list, count = MemoryService.get_by_filter(filter_dict, keywords, page, page_size)
    [memory.update({"memory_type": get_memory_type_human(memory["memory_type"])}) for memory in memory_list]
    return {
        "memory_list": memory_list, "total_count": count
    }


async def get_memory_config(memory_id):
    memory = MemoryService.get_with_owner_name_by_id(memory_id)
    if not memory or not _memory_accessible(memory):
        raise NotFoundException(f"Memory '{memory_id}' not found.")
    return format_ret_data_from_memory(memory)


async def get_memory_messages(memory_id, agent_ids: list[str], keywords: str, page: int=1, page_size: int = 50):
    memory = _require_memory_access(memory_id)
    messages = MessageService.list_message(
        memory.tenant_id, memory_id, agent_ids, keywords, page, page_size)
    agent_name_mapping = {}
    extract_task_mapping = {}
    if messages["message_list"]:
        agent_list = UserCanvasService.get_basic_info_by_canvas_ids([message["agent_id"] for message in messages["message_list"]])
        agent_name_mapping = {agent["id"]: agent["title"] for agent in agent_list}
        task_list = TaskService.get_tasks_progress_by_doc_ids([memory_id])
        if task_list:
            task_list.sort(key=lambda t: t["create_time"]) # asc, use newer when exist more than one task
            for task in task_list:
                # the 'digest' field carries the source_id when a task is created, so use 'digest' as key
                extract_task_mapping.update({int(task["digest"]): task})
    for message in messages["message_list"]:
        message["agent_name"] = agent_name_mapping.get(message["agent_id"], "Unknown")
        message["task"] = extract_task_mapping.get(message["message_id"], {})
        for extract_msg in message["extract"]:
            extract_msg["agent_name"] = agent_name_mapping.get(extract_msg["agent_id"], "Unknown")
    return {"messages": messages, "storage_type": memory.storage_type}


async def add_message(memory_ids: list[str], message_dict: dict):
    """向记忆添加消息

    Args:
        memory_ids: 记忆 ID 列表
        message_dict: 消息字典，包含：
            - agent_id: Agent ID
            - session_id: 会话 ID
            - user_input: 用户输入
            - agent_response: Agent 响应
            - message_type: 消息类型

    Returns:
        添加结果
    """
    accessible_memory_ids = [memory.id for memory in _filter_accessible_memories(memory_ids)]
    if not accessible_memory_ids:
        return False, "Memory not found."
    return await queue_save_to_memory_task(accessible_memory_ids, message_dict)


async def forget_message(memory_id: str, message_id: int):
    memory = _require_memory_access(memory_id)

    forget_time = timestamp_to_date(current_timestamp())
    update_succeed = MessageService.update_message(
        {"memory_id": memory_id, "message_id": int(message_id)},
        {"forget_at": forget_time},
        memory.tenant_id, memory_id)
    if update_succeed:
        return True
    raise Exception(f"Failed to forget message '{message_id}' in memory '{memory_id}'.")


async def update_message_status(memory_id: str, message_id: int, status: bool):
    memory = _require_memory_access(memory_id)

    update_succeed = MessageService.update_message(
        {"memory_id": memory_id, "message_id": int(message_id)},
        {"status": status},
        memory.tenant_id, memory_id)
    if update_succeed:
        return True
    raise Exception(f"Failed to set status for message '{message_id}' in memory '{memory_id}'.")


async def search_message(filter_dict: dict, params: dict):
    """搜索记忆中的消息

    Args:
        filter_dict: 过滤字典，包含：
            - memory_id: 记忆 ID 列表
            - agent_id: Agent ID
            - session_id: 会话 ID
            - user_id: 用户 ID
        params: 搜索参数，包含：
            - query: 查询文本
            - similarity_threshold: 相似度阈值
            - keywords_similarity_weight: 关键词相似度权重
            - top_n: 返回结果数量

    Returns:
        搜索结果
    """
    memory_ids = _split_filter_values(filter_dict.get("memory_id"))
    accessible_memory_ids = [memory.id for memory in _filter_accessible_memories(memory_ids)]
    if not accessible_memory_ids:
        return []
    filter_dict = {**filter_dict, "memory_id": accessible_memory_ids}
    return query_message(filter_dict, params)


async def get_messages(memory_ids: list[str], agent_id: str = "", session_id: str = "", limit: int = 10):
    """从指定记忆获取最近的消息

    Args:
        memory_ids: 记忆 ID 列表
        agent_id: 可选的 Agent ID 用于过滤
        session_id: 可选的会话 ID 用于过滤
        limit: 返回的最大消息数量

    Returns:
        最近的消息列表
    """
    memory_list = _filter_accessible_memories(memory_ids)
    if not memory_list:
        return []
    uids = [memory.tenant_id for memory in memory_list]
    accessible_memory_ids = [memory.id for memory in memory_list]
    res = MessageService.get_recent_messages(
        uids,
        accessible_memory_ids,
        agent_id,
        session_id,
        limit
    )
    return res


async def get_message_content(memory_id: str, message_id: int):
    """获取记忆中特定消息的内容

    Args:
        memory_id: 记忆 ID
        message_id: 消息 ID

    Returns:
        消息内容

    Raises:
        NotFoundException: 当记忆或消息不存在时抛出
    """
    memory = _require_memory_access(memory_id)

    res = MessageService.get_by_message_id(memory_id, message_id, memory.tenant_id)
    if res:
        return res
    raise NotFoundException(f"Message '{message_id}' in memory '{memory_id}' not found.")
