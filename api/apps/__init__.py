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
"""RAGFlow API 应用初始化模块

该模块是 RAGFlow API 应用的主入口，负责：
- 创建和配置 Quart 应用实例
- 设置会话管理、CORS、超时等配置
- 实现用户认证和授权逻辑
- 注册蓝图和路由
- 错误处理
"""

import logging
import os
import sys
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from quart import Blueprint, Quart, request, g, current_app, session, jsonify
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from quart_cors import cors
from common.constants import StatusEnum, RetCode
from api.db.db_models import close_connection, APIToken
from api.db.services import UserService
from api.utils.json_encode import CustomJSONEncoder
from api.utils import commands

from quart_auth import Unauthorized as QuartAuthUnauthorized
from werkzeug.exceptions import Unauthorized as WerkzeugUnauthorized
from quart_schema import QuartSchema
from common import settings
from api.utils.api_utils import server_error_response, get_json_result
from api.constants import API_VERSION
from common.misc_utils import get_uuid

settings.init_settings()

__all__ = ["app"]

UNAUTHORIZED_MESSAGE = "<Unauthorized '401: Unauthorized'>"


def _unauthorized_message(error):
    """获取未授权错误消息

    Args:
        error: 异常对象

    Returns:
        错误消息字符串
    """
    if error is None:
        return UNAUTHORIZED_MESSAGE

    description = getattr(error, "description", None)
    if description:
        return description

    try:
        return repr(error)
    except Exception:
        return UNAUTHORIZED_MESSAGE

app = Quart(__name__)
app = cors(app, allow_origin="*")

# openapi supported
QuartSchema(app)

app.url_map.strict_slashes = False
app.json_encoder = CustomJSONEncoder
app.errorhandler(Exception)(server_error_response)

# Configure Quart timeouts for slow LLM responses (e.g., local Ollama on CPU)
# Default Quart timeouts are 60 seconds which is too short for many LLM backends
app.config["RESPONSE_TIMEOUT"] = int(os.environ.get("QUART_RESPONSE_TIMEOUT", 600))
app.config["BODY_TIMEOUT"] = int(os.environ.get("QUART_BODY_TIMEOUT", 600))

## convince for dev and debug
# app.config["LOGIN_DISABLED"] = True
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_REDIS"] = settings.decrypt_database_config(name="redis")
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("MAX_CONTENT_LENGTH", 1024 * 1024 * 1024)
)
app.config['SECRET_KEY'] = settings.SECRET_KEY
app.secret_key = settings.SECRET_KEY
commands.register_commands(app)

from functools import wraps
from typing import ParamSpec, TypeVar
from collections.abc import Awaitable, Callable
from werkzeug.local import LocalProxy

T = TypeVar("T")
P = ParamSpec("P")


def _load_user():
    """从请求头加载用户信息

    支持多种认证方式：
    1. JWT token 认证
    2. API token 认证
    3. 原始 access_token 认证（用于不带 JWT 的登录令牌）

    Returns:
        用户对象，认证失败返回 None
    """
    jwt = Serializer(secret_key=settings.SECRET_KEY)
    authorization = request.headers.get("Authorization")
    g.user = None
    if not authorization:
        return None

    try:
        access_token = str(jwt.loads(authorization))

        if not access_token or not access_token.strip():
            logging.warning("Authentication attempt with empty access token")
            return None

        # Access tokens should be UUIDs (32 hex characters)
        if len(access_token.strip()) < 32:
            logging.warning(f"Authentication attempt with invalid token format: {len(access_token)} chars")
            return None

        user = UserService.query(
            access_token=access_token, status=StatusEnum.VALID.value
        )
        if user:
            if not user[0].access_token or not user[0].access_token.strip():
                logging.warning(f"User {user[0].email} has empty access_token in database")
                return None
            g.user = user[0]
            return user[0]
    except Exception as e_auth:
        logging.warning(f"load_user from jwt got exception {e_auth}")
        try:
            authorization = request.headers.get("Authorization")
            if len(authorization.split()) == 2:
                token = authorization.split()[1]
                objs = APIToken.query(token=token)
                if objs:
                    user = UserService.query(id=objs[0].tenant_id, status=StatusEnum.VALID.value)
                    if user:
                        if not user[0].access_token or not user[0].access_token.strip():
                            logging.warning(f"User {user[0].email} has empty access_token in database")
                            return None
                        g.user = user[0]
                        return user[0]
                    else:
                        logging.warning(f"load_user: No user found for tenant_id={objs[0].tenant_id} from APIToken")
                else:
                    logging.warning(f"load_user: No APIToken found for token={token[:10]}...")
        except Exception as e_api_token:
            logging.warning(f"load_user from api token got exception {e_api_token}")
        # Fallback: try raw authorization value as access_token (for login tokens sent without JWT)
        try:
            authorization = request.headers.get("Authorization")
            if authorization and len(authorization.split()) == 1:
                # Single value without "Bearer " prefix - try as raw access_token
                access_token = authorization.strip()
                if access_token and len(access_token) >= 32:
                    user = UserService.query(
                        access_token=access_token, status=StatusEnum.VALID.value
                    )
                    if user:
                        if not user[0].access_token or not user[0].access_token.strip():
                            logging.warning(f"User {user[0].email} has empty access_token in database")
                            return None
                        g.user = user[0]
                        return user[0]
        except Exception as e_raw_token:
            logging.warning(f"load_user raw token fallback got exception {e_raw_token}")


current_user = LocalProxy(_load_user)


def login_required(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """限制路由仅对已认证用户访问的装饰器

    该装饰器用于包装路由处理函数（或视图函数），强制要求只有已认证的请求才能访问。
    注意：该装饰器应被路由装饰器包装，而不是反过来，如下所示：

    .. code-block:: python

        @app.route('/')
        @login_required
        async def index():
            ...

    如果请求未认证，将抛出 `quart.exceptions.Unauthorized` 异常。

    Args:
        func: 要装饰的路由处理函数

    Returns:
        包装后的异步函数
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        timing_enabled = os.getenv("RAGFLOW_API_TIMING")
        t_start = time.perf_counter() if timing_enabled else None
        user = current_user
        if timing_enabled:
            logging.info(
                "api_timing login_required auth_ms=%.2f path=%s",
                (time.perf_counter() - t_start) * 1000,
                request.path,
            )
        if not user:  # or not session.get("_user_id"):
            raise QuartAuthUnauthorized()
        return await current_app.ensure_async(func)(*args, **kwargs)

    return wrapper


def login_user(user, remember=False, duration=None, force=False, fresh=True):
    """用户登录

    应该传入实际的用户对象。如果用户的 `is_active` 属性为 ``False``，
    除非 `force` 为 ``True``，否则不会登录。

    登录尝试成功返回 ``True``，失败返回 ``False``（例如用户未激活）。

    Args:
        user: 要登录的用户对象
        remember: 会话过期后是否记住用户。默认为 ``False``
        duration: 记忆 cookie 过期的时间量。如果为 ``None`` 则使用设置中的值。默认为 ``None``
        force: 如果用户未激活，设置为 ``True`` 将强制登录。默认为 ``False``
        fresh: 设置为 ``False`` 将使用标记为非 "fresh" 的会话登录用户。默认为 ``True``

    Returns:
        登录成功返回 True，失败返回 False
    """
    if not force and not user.is_active:
        return False

    session["_user_id"] = user.id
    session["_fresh"] = fresh
    session["_id"] = get_uuid()
    return True


def logout_user():
    """用户登出（无需传入实际用户对象）

    这也会清除 "记住我" cookie（如果存在）。

    Returns:
        成功返回 True
    """
    if "_user_id" in session:
        session.pop("_user_id")

    if "_fresh" in session:
        session.pop("_fresh")

    if "_id" in session:
        session.pop("_id")

    COOKIE_NAME = "remember_token"
    cookie_name = current_app.config.get("REMEMBER_COOKIE_NAME", COOKIE_NAME)
    if cookie_name in request.cookies:
        session["_remember"] = "clear"
        if "_remember_seconds" in session:
            session.pop("_remember_seconds")

    return True


def search_pages_path(page_path):
    """搜索页面路径

    查找指定目录下的所有应用模块文件，包括：
    - *_app.py 文件
    - *sdk/*.py 文件
    - *restful_apis/*.py 文件

    Args:
        page_path: 要搜索的目录路径

    Returns:
        找到的文件路径列表
    """
    app_path_list = [
        path for path in page_path.glob("*_app.py") if not path.name.startswith(".")
    ]
    api_path_list = [
        path for path in page_path.glob("*sdk/*.py") if not path.name.startswith(".")
    ]
    app_path_list.extend(api_path_list)
    restful_api_path_list = [
        path for path in page_path.glob("*restful_apis/*.py") if not path.name.startswith(".")
    ]
    app_path_list.extend(restful_api_path_list)
    return app_path_list


def register_page(page_path):
    """注册页面蓝图

    动态加载并注册页面模块为 Flask 蓝图。

    Args:
        page_path: 页面模块文件路径

    Returns:
        URL 前缀字符串
    """
    path = f"{page_path}"

    page_name = page_path.stem.removesuffix("_app")
    module_name = ".".join(
        page_path.parts[page_path.parts.index("api"): -1] + (page_name,)
    )

    spec = spec_from_file_location(module_name, page_path)
    page = module_from_spec(spec)
    page.app = app
    page.manager = Blueprint(page_name, module_name)
    sys.modules[module_name] = page
    spec.loader.exec_module(page)
    page_name = getattr(page, "page_name", page_name)
    sdk_path = "\\sdk\\" if sys.platform.startswith("win") else "/sdk/"
    restful_api_path = "\\restful_apis\\" if sys.platform.startswith("win") else "/restful_apis/"
    url_prefix = (
        f"/api/{API_VERSION}" if sdk_path in path or restful_api_path in path else f"/{API_VERSION}/{page_name}"
    )

    app.register_blueprint(page.manager, url_prefix=url_prefix)
    return url_prefix


pages_dir = [
    Path(__file__).parent,
    Path(__file__).parent.parent / "api" / "apps",
    Path(__file__).parent.parent / "api" / "apps" / "restful_apis",
    Path(__file__).parent.parent / "api" / "apps" / "sdk",
]

client_urls_prefix = [
    register_page(path) for directory in pages_dir for path in search_pages_path(directory)
]


@app.errorhandler(404)
async def not_found(error):
    """处理 404 错误

    Args:
        error: 错误对象

    Returns:
        JSON 错误响应
    """
    logging.error(f"The requested URL {request.path} was not found")
    message = f"Not Found: {request.path}"
    response = {
        "code": RetCode.NOT_FOUND,
        "message": message,
        "data": None,
        "error": "Not Found",
    }
    return jsonify(response), RetCode.NOT_FOUND


@app.errorhandler(401)
async def unauthorized(error):
    """处理 401 未授权错误

    Args:
        error: 错误对象

    Returns:
        JSON 错误响应
    """
    logging.warning("Unauthorized request")
    return get_json_result(code=RetCode.UNAUTHORIZED, message=_unauthorized_message(error)), RetCode.UNAUTHORIZED


@app.errorhandler(QuartAuthUnauthorized)
async def unauthorized_quart_auth(error):
    """处理 Quart Auth 未授权错误

    Args:
        error: 错误对象

    Returns:
        JSON 错误响应
    """
    logging.warning("Unauthorized request (quart_auth)")
    return get_json_result(code=RetCode.UNAUTHORIZED, message=repr(error)), RetCode.UNAUTHORIZED


@app.errorhandler(WerkzeugUnauthorized)
async def unauthorized_werkzeug(error):
    """处理 Werkzeug 未授权错误

    Args:
        error: 错误对象

    Returns:
        JSON 错误响应
    """
    logging.warning("Unauthorized request (werkzeug)")
    return get_json_result(code=error.code, message=error.description), RetCode.UNAUTHORIZED

@app.teardown_request
def _db_close(exception):
    """请求结束后关闭数据库连接

    Args:
        exception: 请求处理过程中的异常（如果有）
    """
    if exception:
        logging.exception(f"Request failed: {exception}")
    close_connection()
