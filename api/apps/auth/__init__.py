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
"""认证客户端模块

该模块提供了多种认证客户端的实现，包括：
- OAuth2 通用客户端
- OIDC 客户端
- GitHub OAuth 客户端

支持的认证类型：oauth2, oidc, github
"""

from .oauth import OAuthClient
from .oidc import OIDCClient
from .github import GithubOAuthClient


CLIENT_TYPES = {
    "oauth2": OAuthClient,
    "oidc": OIDCClient,
    "github": GithubOAuthClient
}


def get_auth_client(config)->OAuthClient:
    """根据配置获取认证客户端

    根据配置中的类型自动选择合适的认证客户端。
    如果未指定类型，根据配置中的字段自动推断：
    - 如果有 issuer 字段，使用 OIDC 客户端
    - 否则使用 OAuth2 客户端

    Args:
        config: 认证配置字典，包含 type、issuer 等字段

    Returns:
        认证客户端实例

    Raises:
        ValueError: 当指定的类型不被支持时抛出异常
    """
    channel_type = str(config.get("type", "")).lower()
    if channel_type == "":
        if config.get("issuer"):
            channel_type = "oidc"
        else:
            channel_type = "oauth2"
    client_class = CLIENT_TYPES.get(channel_type)
    if not client_class:
        raise ValueError(f"Unsupported type: {channel_type}")

    return client_class(config)
