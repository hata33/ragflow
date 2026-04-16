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
自定义异常类模块

该模块定义了系统中使用的自定义异常类。

主要异常类型：
- AdminException: 管理员相关异常基类
- UserNotFoundError: 用户未找到异常
- UserAlreadyExistsError: 用户已存在异常
- CannotDeleteAdminError: 无法删除管理员异常
- NotAdminError: 非管理员异常
"""


class AdminException(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.type = "admin"
        self.code = code
        self.message = message


class UserNotFoundError(AdminException):
    """
    用户未找到异常

    当系统中找不到指定用户时抛出此异常。

    参数：
        username: 未找到的用户名
    """
    def __init__(self, username):
        super().__init__(f"User '{username}' not found", 404)


class UserAlreadyExistsError(AdminException):
    """
    用户已存在异常

    当尝试创建已存在的用户时抛出此异常。

    参数：
        username: 已存在的用户名
    """
    def __init__(self, username):
        super().__init__(f"User '{username}' already exists", 409)


class CannotDeleteAdminError(AdminException):
    """
    无法删除管理员异常

    当尝试删除管理员账户时抛出此异常。
    系统不允许删除管理员账户。
    """
    def __init__(self):
        super().__init__("Cannot delete admin account", 403)


class NotAdminError(AdminException):
    """
    非管理员异常

    当非管理员用户尝试执行管理员操作时抛出此异常。

    参数：
        username: 尝试执行管理员操作的用户名
    """
    def __init__(self, username):
        super().__init__(f"User '{username}' is not admin", 403)
