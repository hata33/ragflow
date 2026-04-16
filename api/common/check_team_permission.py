#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
团队权限检查模块

该模块提供用于检查知识库和文件的团队权限的函数。

主要功能：
- 检查用户是否有权限访问特定知识库
- 检查用户是否有权限访问特定文件
- 支持团队级别的权限验证
"""

from api.db import TenantPermission
from api.db.db_models import File, Knowledgebase
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.user_service import TenantService


def check_kb_team_permission(kb: dict | Knowledgebase, other: str) -> bool:
    """
    检查用户是否有权限访问知识库

    检查逻辑：
    1. 如果用户是知识库的拥有者，允许访问
    2. 如果知识库权限是团队级别，检查用户是否加入了该团队

    参数：
        kb: 知识库对象或字典
        other: 要检查的用户 ID

    返回：
        bool: 用户是否有权限访问该知识库
    """
    kb = kb.to_dict() if isinstance(kb, Knowledgebase) else kb

    kb_tenant_id = kb["tenant_id"]

    if kb_tenant_id == other:
        return True

    if kb["permission"] != TenantPermission.TEAM:
        return False

    joined_tenants = TenantService.get_joined_tenants_by_user_id(other)
    return any(tenant["tenant_id"] == kb_tenant_id for tenant in joined_tenants)


def check_file_team_permission(file: dict | File, other: str) -> bool:
    """
    检查用户是否有权限访问文件

    检查逻辑：
    1. 如果用户是文件的拥有者，允许访问
    2. 否则检查文件关联的知识库权限
    3. 如果用户对任何一个关联知识库有权限，则允许访问文件

    参数：
        file: 文件对象或字典
        other: 要检查的用户 ID

    返回：
        bool: 用户是否有权限访问该文件
    """
    file = file.to_dict() if isinstance(file, File) else file

    file_tenant_id = file["tenant_id"]
    if file_tenant_id == other:
        return True

    file_id = file["id"]

    kb_ids = [kb_info["kb_id"] for kb_info in FileService.get_kb_id_by_file_id(file_id)]

    for kb_id in kb_ids:
        ok, kb = KnowledgebaseService.get_by_id(kb_id)
        if not ok:
            continue

        if check_kb_team_permission(kb, other):
            return True

    return False
