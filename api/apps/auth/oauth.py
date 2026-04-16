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
通用 OAuth 2.0 认证客户端模块

本模块实现了标准的 OAuth 2.0 认证流程，包括：
- 授权码获取
- 访问令牌交换（同步/异步）
- 用户信息获取（同步/异步）
- 用户信息标准化处理
"""

import urllib.parse
from common.http_client import async_request, sync_request


class UserInfo:
    """用户信息类，用于存储和传递标准化的用户信息"""

    def __init__(self, email, username, nickname, avatar_url):
        """
        初始化用户信息

        Args:
            email: 用户邮箱
            username: 用户名
            nickname: 昵称
            avatar_url: 头像 URL
        """
        self.email = email
        self.username = username
        self.nickname = nickname
        self.avatar_url = avatar_url

    def to_dict(self):
        """
        将用户信息转换为字典格式

        Returns:
            dict: 包含所有用户信息的字典
        """
        return {key: value for key, value in self.__dict__.items()}


class OAuthClient:
    """OAuth 2.0 客户端基类"""

    def __init__(self, config):
        """
        初始化 OAuth 客户端

        Args:
            config: OAuth 配置字典，包含以下字段：
                - client_id: 客户端 ID
                - client_secret: 客户端密钥
                - authorization_url: 授权端点 URL
                - token_url: 令牌端点 URL
                - userinfo_url: 用户信息端点 URL
                - redirect_uri: 重定向 URI
                - scope: 权限范围（可选）
        """
        self.client_id = config["client_id"]
        self.client_secret = config["client_secret"]
        self.authorization_url = config["authorization_url"]
        self.token_url = config["token_url"]
        self.userinfo_url = config["userinfo_url"]
        self.redirect_uri = config["redirect_uri"]
        self.scope = config.get("scope", None)

        self.http_request_timeout = 7


    def get_authorization_url(self, state=None):
        """
        生成授权 URL

        Args:
            state: 用于防止 CSRF 攻击的状态参数（可选）

        Returns:
            str: 完整的授权 URL
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
        }
        if self.scope:
            params["scope"] = self.scope
        if state:
            params["state"] = state
        authorization_url = f"{self.authorization_url}?{urllib.parse.urlencode(params)}"
        return authorization_url


    def exchange_code_for_token(self, code):
        """
        使用授权码交换访问令牌（同步方法）

        Args:
            code: 授权码

        Returns:
            dict: 包含访问令牌等信息的响应数据

        Raises:
            ValueError: 令牌交换失败时抛出异常
        """
        try:
            payload = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code"
            }
            response = sync_request(
                "POST",
                self.token_url,
                data=payload,
                headers={"Accept": "application/json"},
                timeout=self.http_request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise ValueError(f"Failed to exchange authorization code for token: {e}")

    async def async_exchange_code_for_token(self, code):
        """
        使用授权码交换访问令牌（异步方法）

        Args:
            code: 授权码

        Returns:
            dict: 包含访问令牌等信息的响应数据

        Raises:
            ValueError: 令牌交换失败时抛出异常
        """
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            response = await async_request(
                "POST",
                self.token_url,
                data=payload,
                headers={"Accept": "application/json"},
                timeout=self.http_request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise ValueError(f"Failed to exchange authorization code for token: {e}")


    def fetch_user_info(self, access_token, **kwargs):
        """
        使用访问令牌获取用户信息（同步方法）

        Args:
            access_token: 访问令牌
            **kwargs: 其他可选参数

        Returns:
            UserInfo: 标准化的用户信息对象

        Raises:
            ValueError: 获取用户信息失败时抛出异常
        """
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = sync_request("GET", self.userinfo_url, headers=headers, timeout=self.http_request_timeout)
            response.raise_for_status()
            user_info = response.json()
            return self.normalize_user_info(user_info)
        except Exception as e:
            raise ValueError(f"Failed to fetch user info: {e}")

    async def async_fetch_user_info(self, access_token, **kwargs):
        """
        使用访问令牌获取用户信息（异步方法）

        Args:
            access_token: 访问令牌
            **kwargs: 其他可选参数

        Returns:
            UserInfo: 标准化的用户信息对象

        Raises:
            ValueError: 获取用户信息失败时抛出异常
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            response = await async_request(
                "GET",
                self.userinfo_url,
                headers=headers,
                timeout=self.http_request_timeout,
            )
            response.raise_for_status()
            user_info = response.json()
            return self.normalize_user_info(user_info)
        except Exception as e:
            raise ValueError(f"Failed to fetch user info: {e}")


    def normalize_user_info(self, user_info):
        """
        标准化用户信息

        Args:
            user_info: OAuth 提供商返回的原始用户信息

        Returns:
            UserInfo: 标准化后的用户信息对象
        """
        email = user_info.get("email")
        username = user_info.get("username", str(email).split("@")[0])
        nickname = user_info.get("nickname", username)
        avatar_url = user_info.get("avatar_url", None)
        if avatar_url is None:
            avatar_url = user_info.get("picture", "")
        return UserInfo(email=email, username=username, nickname=nickname, avatar_url=avatar_url)
