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
API令牌管理模块

该模块提供了用于管理RAGFlow系统外部访问API令牌的端点。
包括创建、列出和删除令牌，以及检索使用统计信息的功能。

主要功能：
- 为对话和智能体创建新的API令牌
- 列出特定对话的现有令牌
- 按租户ID删除令牌
- 检索使用统计信息（PV、UV、速度、令牌数、轮次、点赞数）
"""

from datetime import datetime, timedelta
from quart import request
from api.db.db_models import APIToken
from api.db.services.api_service import APITokenService, API4ConversationService
from api.db.services.user_service import UserTenantService
from api.utils.api_utils import generate_confirmation_token, get_data_error_result, get_json_result, get_request_json, server_error_response, validate_request
from common.time_utils import current_timestamp, datetime_format
from api.apps import login_required, current_user


@manager.route('/new_token', methods=['POST'])  # noqa: F821
@login_required
async def new_token():
    """
    创建新的API令牌用于外部访问

    该端点生成新的API令牌，可用于外部访问RAGFlow API。
    令牌可以与对话或智能体（画布）关联。

    请求体：
        - dialog_id (str, 可选): 要关联的对话ID
        - canvas_id (str, 可选): 要关联的智能体画布ID

    返回：
        JSON响应，包含创建的令牌对象：
        - token: 生成的API令牌
        - tenant_id: 租户ID
        - dialog_id: 关联的对话/画布ID
        - source: 如果提供canvas_id则为"agent"，否则为None
        - create_time: 令牌创建时间戳
        - create_date: 令牌创建日期

    异常：
        404: 未找到租户
        500: 令牌创建失败
    """
    req = await get_request_json()
    try:
        tenants = UserTenantService.query(user_id=current_user.id)
        if not tenants:
            return get_data_error_result(message="Tenant not found!")

        tenant_id = tenants[0].tenant_id
        obj = {"tenant_id": tenant_id, "token": generate_confirmation_token(),
               "create_time": current_timestamp(),
               "create_date": datetime_format(datetime.now()),
               "update_time": None,
               "update_date": None
               }
        if req.get("canvas_id"):
            obj["dialog_id"] = req["canvas_id"]
            obj["source"] = "agent"
        else:
            obj["dialog_id"] = req["dialog_id"]

        if not APITokenService.save(**obj):
            return get_data_error_result(message="Fail to new a dialog!")

        return get_json_result(data=obj)
    except Exception as e:
        return server_error_response(e)


@manager.route('/token_list', methods=['GET'])  # noqa: F821
@login_required
def token_list():
    """
    列出特定对话或画布的所有API令牌

    该端点检索与给定对话ID或画布ID关联的所有API令牌。

    查询参数：
        - dialog_id (str, 可选): 对话ID
        - canvas_id (str, 可选): 智能体画布ID

    返回：
        JSON响应，包含令牌对象列表：
        - token: API令牌
        - tenant_id: 租户ID
        - dialog_id: 关联的对话/画布ID
        - source: 令牌来源（agent或dialog）
        - create_time: 创建时间戳
        - update_time: 最后更新时间戳

    异常：
        404: 未找到租户
        500: 检索失败
    """
    try:
        tenants = UserTenantService.query(user_id=current_user.id)
        if not tenants:
            return get_data_error_result(message="Tenant not found!")

        id = request.args["dialog_id"] if "dialog_id" in request.args else request.args["canvas_id"]
        objs = APITokenService.query(tenant_id=tenants[0].tenant_id, dialog_id=id)
        return get_json_result(data=[o.to_dict() for o in objs])
    except Exception as e:
        return server_error_response(e)


@manager.route('/rm', methods=['POST'])  # noqa: F821
@validate_request("tokens", "tenant_id")
@login_required
async def rm():
    """
    删除API令牌

    该端点删除指定租户的一个或多个API令牌。

    请求体：
        - tokens (list): 要删除的令牌字符串列表
        - tenant_id (str): 用于授权的租户ID

    返回：
        JSON响应，成功时data=True

    异常：
        500: 删除失败
    """
    req = await get_request_json()
    try:
        for token in req["tokens"]:
            APITokenService.filter_delete(
                [APIToken.tenant_id == req["tenant_id"], APIToken.token == token])
        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)


@manager.route('/stats', methods=['GET'])  # noqa: F821
@login_required
def stats():
    """
    检索API对话的使用统计信息

    该端点提供与当前用户租户关联的API对话的使用指标，
    包括页面浏览量、独立访客、响应速度、令牌使用量、对话轮次和点赞数。

    查询参数：
        - from_date (str, 可选): 开始日期，格式为YYYY-MM-DD HH:MM:SS（默认：7天前）
        - to_date (str, 可选): 结束日期，格式为YYYY-MM-DD HH:MM:SS（默认：现在）
        - canvas_id (str, 可选): 如果存在，统计信息为智能体对话的

    返回：
        JSON响应，包含统计数组：
        - pv: (日期, 页面浏览量)元组列表
        - uv: (日期, 独立访客数)元组列表
        - speed: (日期, 每秒令牌数)元组列表
        - tokens: (日期, 令牌数/千)元组列表
        - round: (日期, 对话轮次数)元组列表
        - thumb_up: (日期, 点赞数)元组列表

    异常：
        404: 未找到租户
        500: 统计信息检索失败
    """
    try:
        tenants = UserTenantService.query(user_id=current_user.id)
        if not tenants:
            return get_data_error_result(message="Tenant not found!")
        objs = API4ConversationService.stats(
            tenants[0].tenant_id,
            request.args.get(
                "from_date",
                (datetime.now() -
                 timedelta(
                     days=7)).strftime("%Y-%m-%d 00:00:00")),
            request.args.get(
                "to_date",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "agent" if "canvas_id" in request.args else None)

        res = {"pv": [], "uv": [], "speed": [], "tokens": [], "round": [], "thumb_up": []}

        for obj in objs:
            dt = obj["dt"]
            res["pv"].append((dt, obj["pv"]))
            res["uv"].append((dt, obj["uv"]))
            res["speed"].append((dt, float(obj["tokens"]) / (float(obj["duration"]) + 0.1))) # +0.1 to avoid division by zero
            res["tokens"].append((dt, float(obj["tokens"]) / 1000.0)) # convert to thousands
            res["round"].append((dt, obj["round"]))
            res["thumb_up"].append((dt, obj["thumb_up"]))

        return get_json_result(data=res)
    except Exception as e:
        return server_error_response(e)
