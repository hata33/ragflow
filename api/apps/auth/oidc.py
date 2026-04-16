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
OpenID Connect (OIDC) 认证客户端模块

本模块实现了 OIDC 认证流程，包括：
- OIDC 发现文档自动获取
- ID Token 验证和解析
- JWT 签名验证
- 用户信息获取和标准化
"""

import jwt
from common.http_client import sync_request
from .oauth import OAuthClient


class OIDCClient(OAuthClient):
    """OpenID Connect 认证客户端类"""

    def __init__(self, config):
        """
        初始化 OIDC 客户端

        Args:
            config: OIDC 配置字典，必须包含 issuer 字段。
                   其他配置将从 OIDC 发现文档自动获取

        Raises:
            ValueError: 缺少 issuer 配置或获取发现文档失败时抛出异常
        """
        self.issuer = config.get("issuer")
        if not self.issuer:
            raise ValueError("Missing issuer in configuration.")

        oidc_metadata = self._load_oidc_metadata(self.issuer)
        config.update({
            'issuer': oidc_metadata['issuer'],
            'jwks_uri': oidc_metadata['jwks_uri'],
            'authorization_url': oidc_metadata['authorization_endpoint'],
            'token_url': oidc_metadata['token_endpoint'],
            'userinfo_url': oidc_metadata['userinfo_endpoint']
        })

        super().__init__(config)
        self.issuer = config['issuer']
        self.jwks_uri = config['jwks_uri']


    @staticmethod
    def _load_oidc_metadata(issuer):
        """
        从 OIDC 发现文档加载配置元数据

        Args:
            issuer: OIDC 提供商的 issuer URL

        Returns:
            dict: 包含授权端点、令牌端点、用户信息端点等配置的字典

        Raises:
            ValueError: 获取发现文档失败时抛出异常
        """
        try:
            metadata_url = f"{issuer}/.well-known/openid-configuration"
            response = sync_request("GET", metadata_url, timeout=7)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise ValueError(f"Failed to fetch OIDC metadata: {e}")


    def parse_id_token(self, id_token):
        """
        解析并验证 OIDC ID Token（JWT 格式）

        Args:
            id_token: JWT 格式的 ID Token

        Returns:
            dict: 解码后的 Token 内容

        Raises:
            ValueError: Token 解析或验证失败时抛出异常
        """
        try:
            # Decode JWT header without verifying signature
            headers = jwt.get_unverified_header(id_token)
            
            # OIDC usually uses `RS256` for signing
            alg = headers.get("alg", "RS256")

            # Use PyJWT's PyJWKClient to fetch JWKS and find signing key
            jwks_cli = jwt.PyJWKClient(self.jwks_uri)
            signing_key = jwks_cli.get_signing_key_from_jwt(id_token).key

            # Decode and verify signature
            decoded_token = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=[alg],  
                audience=str(self.client_id),
                issuer=self.issuer,
            )
            return decoded_token
        except Exception as e:
            raise ValueError(f"Error parsing ID Token: {e}")


    def fetch_user_info(self, access_token, id_token=None, **kwargs):
        """
        获取用户信息（同步方法）

        Args:
            access_token: 访问令牌
            id_token: ID Token（可选），用于获取额外的用户信息
            **kwargs: 其他可选参数

        Returns:
            UserInfo: 标准化的用户信息对象
        """
        user_info = {}
        if id_token:
            user_info = self.parse_id_token(id_token)
        user_info.update(super().fetch_user_info(access_token).to_dict())
        return self.normalize_user_info(user_info)

    async def async_fetch_user_info(self, access_token, id_token=None, **kwargs):
        """
        获取用户信息（异步方法）

        Args:
            access_token: 访问令牌
            id_token: ID Token（可选），用于获取额外的用户信息
            **kwargs: 其他可选参数

        Returns:
            UserInfo: 标准化的用户信息对象
        """
        user_info = {}
        if id_token:
            user_info = self.parse_id_token(id_token)
        user_info.update((await super().async_fetch_user_info(access_token)).to_dict())
        return self.normalize_user_info(user_info)


    def normalize_user_info(self, user_info):
        """
        标准化用户信息

        Args:
            user_info: OIDC 提供商返回的原始用户信息

        Returns:
            UserInfo: 标准化后的用户信息对象
        """
        return super().normalize_user_info(user_info)
