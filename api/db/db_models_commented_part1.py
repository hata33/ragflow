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
数据库模型模块 - 逐行注释版本

本模块定义了系统的核心数据库模型，基于 Peewee ORM 实现。

主要功能：
- 自定义字段类型（JSONField、ListField、SerializedField、LongTextField）
- 基础模型类（BaseModel）提供通用的数据库操作方法
- 数据库连接池管理（支持 MySQL、PostgreSQL、OceanBase）
- 数据迁移支持
- 模型字段类型判断和转换工具

核心类：
- TextFieldType: 文本字段类型枚举（针对不同数据库）
- LongTextField: 长文本字段（自动适配数据库类型）
- JSONField: JSON 字段（自动序列化/反序列化）
- ListField: 列表字段（继承自 JSONField）
- SerializedField: 序列化字段（支持 PICKLE 和 JSON）
- BaseModel: 所有数据库模型的基类
"""

# ==================== 标准库导入 ====================
import hashlib                    # 哈希算法，用于生成唯一标识
import inspect                   # 反射工具，用于检查类和函数
import logging                    # 日志记录
import operator                   # 操作符函数，用于属性访问
import os                         # 操作系统接口
import sys                        # 系统相关参数和函数
import time                       # 时间处理
import typing                     # 类型提示支持
from datetime import datetime, timezone    # 日期时间处理
from enum import Enum                         # 枚举类型支持
from functools import wraps                   # 函数装饰器工具

# ==================== 第三方库导入 ====================
from quart_auth import AuthUser                 # Quart认证用户类
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer  # 安全URL序列化器
from peewee import (                             # Peewee ORM核心组件
    fn,                                          # 聚合函数
    InterfaceError,                              # 数据库接口错误
    OperationalError,                            # 数据库操作错误
    ProgrammingError,                            # 数据库编程错误
    BigIntegerField,                             # 大整数字段
    BooleanField,                                # 布尔字段
    CharField,                                   # 字符字段
    CompositeKey,                                # 复合主键
    DateTimeField,                               # 日期时间字段
    Field,                                       # 字段基类
    FloatField,                                  # 浮点数字段
    IntegerField,                                # 整数字段
    Metadata,                                    # 元数据
    Model,                                       # 模型基类
    TextField,                                   # 文本字段
    PrimaryKeyField,                             # 主键字段
)
from playhouse.migrate import MySQLMigrator, PostgresqlMigrator, migrate  # 数据库迁移工具
from playhouse.pool import PooledMySQLDatabase, PooledPostgresqlDatabase   # 连接池支持

# ==================== 项目内部导入 ====================
from api import utils                            # API工具函数
from api.db import SerializedType                 # 序列化类型枚举
from api.utils.json_encode import json_dumps, json_loads  # JSON编解码工具
from api.utils.configs import deserialize_b64, serialize_b64  # Base64序列化工具

from common.time_utils import current_timestamp, timestamp_to_date, date_string_to_timestamp  # 时间工具
from common.decorator import singleton             # 单例装饰器
from common.constants import ParserType            # 解析器类型常量
from common import settings                        # 全局配置


# ==================== 常量定义 ====================

# 连续型字段类型集合：可用于范围查询（整数、浮点数、日期时间）
CONTINUOUS_FIELD_TYPE = {IntegerField, FloatField, DateTimeField}

# 自动时间戳字段前缀集合：这些字段会自动设置对应的日期字段
AUTO_DATE_TIMESTAMP_FIELD_PREFIX = {"create", "start", "end", "update", "read_access", "write_access"}


# ==================== 字段类型定义 ====================

class TextFieldType(Enum):
    """
    文本字段类型枚举

    根据数据库类型返回对应的 LONGTEXT/TEXT 类型。
    不同数据库对长文本的支持不同：
    - MySQL/OceanBase: 使用 LONGTEXT
    - PostgreSQL: 使用 TEXT
    """
    MYSQL = "LONGTEXT"        # MySQL长文本类型
    OCEANBASE = "LONGTEXT"    # OceanBase长文本类型
    POSTGRES = "TEXT"         # PostgreSQL文本类型


class LongTextField(TextField):
    """
    长文本字段类

    自动根据当前数据库类型选择合适的文本字段类型。
    继承自Peewee的TextField，但field_type是动态确定的。
    """
    # 根据配置的数据库类型动态设置字段类型
    field_type = TextFieldType[settings.DATABASE_TYPE.upper()].value


class JSONField(LongTextField):
    """
    JSON 字段类

    自动处理 JSON 数据的序列化（存入数据库）和反序列化（从数据库读取）。
    适用于存储字典、列表等JSON格式数据。

    Attributes:
        default_value: 默认值为空字典

    Usage:
        class MyModel(DataBaseModel):
            config = JSONField()  # 存储JSON配置
    """
    default_value = {}                    # 默认空字典

    def __init__(self, object_hook=None, object_pairs_hook=None, **kwargs):
        """
        初始化JSON字段

        Args:
            object_hook: 自定义对象钩子函数，用于反序列化时重建对象
            object_pairs_hook: 自定义对象对钩子函数，用于处理键值对
            **kwargs: 其他字段参数
        """
        self._object_hook = object_hook              # 保存对象钩子
        self._object_pairs_hook = object_pairs_hook    # 保存对象对钩子
        super().__init__(**kwargs)                    # 调用父类初始化

    def db_value(self, value):
        """
        将 Python 对象转换为 JSON 字符串存入数据库

        Args:
            value: Python对象（字典、列表等）

        Returns:
            str: JSON格式的字符串
        """
        # 如果值为None，使用默认值
        if value is None:
            value = self.default_value
        # 使用自定义的json_dumps序列化
        return json_dumps(value)

    def python_value(self, value):
        """
        将数据库中的 JSON 字符串转换为 Python 对象

        Args:
            value: 数据库中的JSON字符串

        Returns:
            dict/list: Python对象
        """
        # 如果值为空，返回默认值
        if not value:
            return self.default_value
        # 使用自定义的json_loads反序列化
        return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)


class ListField(JSONField):
    """
    列表字段类

    继承自 JSONField，专门用于存储列表类型数据。

    Usage:
        class MyModel(DataBaseModel):
            tags = ListField()  # 存储标签列表
    """
    default_value = []                     # 默认空列表，区别于JSONField的字典


class SerializedField(LongTextField):
    """
    序列化字段类

    支持多种序列化方式（PICKLE、JSON）的字段类型。
    PICKLE: Python二进制序列化，支持所有Python对象
    JSON: 文本序列化，可读性好但只支持基本类型

    Args:
        serialized_type: 序列化类型（PICKLE 或 JSON）
        object_hook: 自定义对象钩子函数
        object_pairs_hook: 自定义对象对钩子函数
    """
    def __init__(self, serialized_type=SerializedType.PICKLE, object_hook=None, object_pairs_hook=None, **kwargs):
        """
        初始化序列化字段

        Args:
            serialized_type: 序列化类型，默认为PICKLE
            object_hook: 自定义对象钩子
            object_pairs_hook: 自定义对象对钩子
            **kwargs: 其他字段参数
        """
        self._serialized_type = serialized_type           # 保存序列化类型
        self._object_hook = object_hook                     # 保存对象钩子
        self._object_pairs_hook = object_pairs_hook         # 保存对象对钩子
        super().__init__(**kwargs)                         # 调用父类初始化

    def db_value(self, value):
        """
        将 Python 对象序列化后存入数据库

        Args:
            value: 要序列化的Python对象

        Returns:
            str: 序列化后的字符串（Base64编码的Pickle或JSON）
        """
        # PICKLE序列化：使用Base64编码
        if self._serialized_type == SerializedType.PICKLE:
            return serialize_b64(value, to_str=True)
        # JSON序列化：使用JSON字符串（带类型信息）
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return None
            return json_dumps(value, with_type=True)
        # 不支持的序列化类型
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")

    def python_value(self, value):
        """
        从数据库读取序列化数据并反序列化为 Python 对象

        Args:
            value: 数据库中的序列化字符串

        Returns:
            object: 反序列化后的Python对象
        """
        # PICKLE反序列化：从Base64解码
        if self._serialized_type == SerializedType.PICKLE:
            return deserialize_b64(value)
        # JSON反序列化：解析JSON字符串
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return {}
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        # 不支持的序列化类型
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")


# ==================== 工具函数 ====================

def is_continuous_field(cls: typing.Type) -> bool:
    """
    判断字段类型是否为连续型（可用于范围查询）

    连续型字段包括：IntegerField、FloatField、DateTimeField
    这些字段支持范围查询如 BETWEEN、>=、<= 等

    Args:
        cls: 字段类型

    Returns:
        bool: 是否为连续型字段
    """
    # 检查是否直接在连续字段集合中
    if cls in CONTINUOUS_FIELD_TYPE:
        return True
    # 递归检查父类是否为连续字段
    for p in cls.__bases__:
        if p in CONTINUOUS_FIELD_TYPE:
            return True
        # 继续递归（排除Field和object基类）
        elif p is not Field and p is not object:
            if is_continuous_field(p):
                return True
    # 不是连续字段
    else:
        return False


def auto_date_timestamp_field():
    """
    获取所有自动时间戳字段名称集合

    Returns:
        set: 包含所有带 _time 后缀的字段名
    """
    return {f"{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def auto_date_timestamp_db_field():
    """
    获取所有自动时间戳数据库字段名称集合（带 f_ 前缀）

    Returns:
        set: 包含所有带 f__time 后缀的数据库字段名
    """
    return {f"f_{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def remove_field_name_prefix(field_name):
    """
    移除字段名的前缀（f_）

    数据库字段通常有 f_ 前缀，但业务层使用时不带前缀。
    此函数用于在两者之间转换。

    Args:
        field_name: 数据库字段名（可能带 f_ 前缀）

    Returns:
        str: 移除前缀后的字段名

    Example:
        >>> remove_field_name_prefix("f_name")
        'name'
        >>> remove_field_name_prefix("name")
        'name'
    """
    return field_name[2:] if field_name.startswith("f_") else field_name


# ==================== 基础模型类 ====================

class BaseModel(Model):
    """
    基础模型类

    所有数据库模型的基类，提供通用的数据库操作方法和字段。

    Attributes:
        create_time: 创建时间（毫秒时间戳）
        create_date: 创建日期
        update_time: 更新时间（毫秒时间戳）
        update_date: 更新日期

    Features:
        - 自动时间戳管理
        - 统一的字典转换方法
        - 通用查询接口
        - 自动字段标准化
    """
    # ==================== 基础字段定义 ====================
    create_time = BigIntegerField(null=True, index=True)      # 创建时间（毫秒时间戳）
    create_date = DateTimeField(null=True, index=True)        # 创建日期（datetime对象）
    update_time = BigIntegerField(null=True, index=True)      # 更新时间（毫秒时间戳）
    update_date = DateTimeField(null=True, index=True)        # 更新日期（datetime对象）

    # ==================== 实例方法 ====================

    def to_json(self):
        """
        将模型转换为 JSON 格式（已过时）

        .. deprecated::
            使用 to_dict() 方法代替

        Returns:
            dict: 模型数据的字典表示
        """
        return self.to_dict()                          # 调用新方法

    def to_dict(self):
        """
        将模型转换为字典

        返回模型内部的数据字典，包含所有字段的当前值。

        Returns:
            dict: 模型数据的字典表示，键为字段名，值为字段值
        """
        return self.__dict__["__data__"]              # 访问Peewee内部数据字典

    def to_human_model_dict(self, only_primary_with: list = None):
        """
        将模型转换为人类可读的字典格式（移除字段前缀）

        此方法用于将模型数据转换为适合返回给前端的格式，
        会自动移除数据库字段前缀（如 f_）。

        Args:
            only_primary_with: 可选，指定除主键外需要包含的字段列表。
                              如果为 None，则返回所有字段；
                              如果指定，则只返回主键和指定字段

        Returns:
            dict: 人类可读的模型字典，字段名已移除前缀
        """
        model_dict = self.__dict__["__data__"]        # 获取内部数据字典

        if not only_primary_with:
            # 返回所有字段，移除前缀
            return {remove_field_name_prefix(k): v for k, v in model_dict.items()}

        # 只返回主键和指定字段
        human_model_dict = {}
        # 添加主键字段
        for k in self._meta.primary_key.field_names:
            human_model_dict[remove_field_name_prefix(k)] = model_dict[k]
        # 添加指定字段（带f_前缀的数据库字段）
        for k in only_primary_with:
            human_model_dict[k] = model_dict[f"f_{k}"]
        return human_model_dict

    # ==================== 属性方法 ====================

    @property
    def meta(self) -> Metadata:
        """
        获取模型的元数据对象

        Returns:
            Metadata: Peewee 模型的元数据对象，包含表结构、字段等信息
        """
        return self._meta                             # 返回Peewee元数据

    # ==================== 类方法 ====================

    @classmethod
    def get_primary_keys_name(cls):
        """
        获取主键字段名称列表

        Returns:
            list: 主键字段名称列表。
                 对于复合主键返回所有字段名，
                 单一主键返回包含单个字段名的列表
        """
        # 判断是否为复合主键
        return cls._meta.primary_key.field_names if isinstance(cls._meta.primary_key, CompositeKey) else [cls._meta.primary_key.name]

    @classmethod
    def getter_by(cls, attr):
        """
        获取指定属性的访问器

        Args:
            attr: 属性名称

        Returns:
            callable: 属性的 getter 函数
        """
        return operator.attrgetter(attr)(cls)         # 返回属性访问器

    @classmethod
    def query(cls, reverse=None, order_by=None, **kwargs):
        """
        查询数据库记录的通用方法

        支持多种查询条件：
        - 精确匹配：field=value
        - 范围查询（连续字段）：field=[start, end]
        - IN 查询：field=[value1, value2, ...]
        - 排序：支持正序和倒序

        Args:
            reverse: 排序方向，True=倒序，False=正序，None=不排序
            order_by: 排序字段名，默认为 create_time
            **kwargs: 查询条件键值对

        Returns:
            list: 匹配的模型对象列表
        """
        filters = []                                 # 查询条件列表
        for f_n, f_v in kwargs.items():               # 遍历所有查询条件
            attr_name = "%s" % f_n                     # 转换为字符串属性名
            # 跳过不存在的字段和空值
            if not hasattr(cls, attr_name) or f_v is None:
                continue
            # 处理列表/集合类型的查询值
            if type(f_v) in {list, set}:
                f_v = list(f_v)                         # 转换为列表
                # 连续字段（整数、浮点数、日期时间）支持范围查询
                if is_continuous_field(type(getattr(cls, attr_name))):
                    if len(f_v) == 2:                   # 范围查询需要恰好2个值
                        # 处理日期字符串转换
                        for i, v in enumerate(f_v):
                            if isinstance(v, str) and f_n in auto_date_timestamp_field():
                                # time type: %Y-%m-%d %H:%M:%S
                                f_v[i] = date_string_to_timestamp(v)
                        lt_value = f_v[0]                 # 范围下限
                        gt_value = f_v[1]                 # 范围上限
                        # 范围查询：BETWEEN
                        if lt_value is not None and gt_value is not None:
                            filters.append(cls.getter_by(attr_name).between(lt_value, gt_value))
                        # 单边查询：>= 或 <=
                        elif lt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) >= lt_value)
                        elif gt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) <= gt_value)
                else:
                    # 非连续字段使用 IN 查询
                    filters.append(operator.attrgetter(attr_name)(cls) << f_v)
            else:
                # 精确匹配
                filters.append(operator.attrgetter(attr_name)(cls) == f_v)
        # 如果有查询条件，执行查询
        if filters:
            query_records = cls.select().where(*filters)    # 构建查询
            # 应用排序
            if reverse is not None:                         # 需要排序
                if not order_by or not hasattr(cls, f"{order_by}"):
                    order_by = "create_time"              # 默认按创建时间排序
                if reverse is True:                         # 倒序
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").desc())
                elif reverse is False:                       # 正序
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").asc())
            # 返回查询结果列表
            return [query_record for query_record in query_records]
        else:
            return []                                   # 无查询条件返回空列表

    @classmethod
    def insert(cls, __data=None, **insert):
        """
        插入新记录，自动设置创建时间

        Args:
            __data: 字典形式的数据
            **insert: 关键字参数形式的数据

        Returns:
            int: 新创建的记录 ID
        """
        # 自动设置创建时间戳
        if isinstance(__data, dict) and __data:
            __data[cls._meta.combined["create_time"]] = current_timestamp()
        if insert:
            insert["create_time"] = current_timestamp()

        return super().insert(__data, **insert)       # 调用父类插入方法

    # update and insert will call this method
    @classmethod
    def _normalize_data(cls, data, kwargs):
        """
        标准化数据，自动设置更新时间和日期字段

        此方法在插入和更新时自动调用，用于：
        1. 自动设置 update_time 为当前时间戳
        2. 根据 *_time 字段自动设置对应的 *_date 字段

        Args:
            data: 原始数据字典
            kwargs: 额外的关键字参数

        Returns:
            dict: 标准化后的数据字典
        """
        # 调用父类标准化方法
        normalized = super()._normalize_data(data, kwargs)
        if not normalized:
            return {}                                  # 空数据返回空字典

        # 自动设置更新时间
        normalized[cls._meta.combined["update_time"]] = current_timestamp()

        # 自动根据时间戳设置对应的日期字段
        for f_n in AUTO_DATE_TIMESTAMP_FIELD_PREFIX:
            # 如果同时存在 *_time 和 *_date 字段，且 *_time 有值
            if {f"{f_n}_time", f"{f_n}_date"}.issubset(cls._meta.combined.keys()) and \
               cls._meta.combined[f"{f_n}_time"] in normalized and \
               normalized[cls._meta.combined[f"{f_n}_time"]] is not None:
                # 根据时间戳设置日期
                normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(normalized[cls._meta.combined[f"{f_n}_time"]])

        return normalized


# ==================== 序列化字段类 ====================

class JsonSerializedField(SerializedField):
    """
    JSON 序列化字段

    使用 JSON 格式进行序列化的字段类型，继承自 SerializedField。
    自动处理自定义对象的序列化和反序列化。

    Args:
        object_hook: 自定义对象钩子函数，用于反序列化时重建对象
        object_pairs_hook: 自定义对象对钩子函数
        **kwargs: 其他字段参数
    """
    def __init__(self, object_hook=utils.from_dict_hook, object_pairs_hook=None, **kwargs):
        """
        初始化JSON序列化字段

        Args:
            object_hook: 自定义对象钩子，默认为utils.from_dict_hook
            object_pairs_hook: 自定义对象对钩子
            **kwargs: 其他字段参数
        """
        # 调用父类初始化，指定序列化类型为JSON
        super(JsonSerializedField, self).__init__(
            serialized_type=SerializedType.JSON,
            object_hook=object_hook,
            object_pairs_hook=object_pairs_hook,
            **kwargs
        )


# ==================== 数据库连接池类 ====================

class RetryingPooledMySQLDatabase(PooledMySQLDatabase):
    """
    带重试机制的 MySQL 连接池数据库

    在 PooledMySQLDatabase 基础上添加了自动重试机制，
    用于处理数据库连接中断等问题。

    Attributes:
        max_retries: 最大重试次数，默认 5 次
        retry_delay: 初始重试延迟（秒），默认 1 秒，后续按指数增长
    """
    def __init__(self, *args, **kwargs):
        """
        初始化带重试机制的MySQL连接池

        Args:
            *args: 位置参数传递给PooledMySQLDatabase
            **kwargs: 关键字参数，包括max_retries和retry_delay
        """
        # 从kwargs中提取重试参数
        self.max_retries = kwargs.pop("max_retries", 5)      # 最大重试次数
        self.retry_delay = kwargs.pop("retry_delay", 1)       # 重试延迟
        super().__init__(*args, **kwargs)                     # 调用父类初始化

    def execute_sql(self, sql, params=None, commit=True):
        """
        执行 SQL 语句，支持连接失败自动重试

        Args:
            sql: SQL 语句
            params: SQL 参数
            commit: 是否提交事务

        Returns:
            cursor: 查询结果游标
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)  # 尝试执行SQL
            except (OperationalError, InterfaceError) as e:
                # MySQL特定错误码
                error_codes = [2013, 2006]                     # 2013:连接断开, 2006:服务器已断开
                error_messages = ['', 'Lost connection']
                # 判断是否为可重试错误
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Database connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()          # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    logging.error(f"DB execution failure: {e}")
                    raise                                          # 重试次数用尽，抛出异常
        return None

    def _handle_connection_loss(self):
        """
        处理连接丢失，尝试重新连接
        """
        # 注意：关闭所有连接的方法被注释掉
        # self.close_all()
        # self.connect()
        try:
            self.close()                                    # 关闭当前连接
        except Exception:
            pass                                        # 忽略关闭异常
        try:
            self.connect()                                   # 尝试重新连接
        except Exception as e:
            logging.error(f"Failed to reconnect: {e}")
            time.sleep(0.1)                                # 短暂延迟
            try:
                self.connect()                           # 第二次重试
            except Exception as e2:
                logging.error(f"Failed to reconnect on second attempt: {e2}")
                raise                                          # 第二次重试失败，抛出异常

    def begin(self):
        """
        开始事务，支持连接失败自动重试

        Returns:
            transaction: 事务上下文
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()                        # 尝试开始事务
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                # 判断是否为可重试错误
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()              # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    raise                                          # 重试次数用尽，抛出异常
        return None


class RetryingPooledPostgresqlDatabase(PooledPostgresqlDatabase):
    """
    带重试机制的 PostgreSQL 连接池数据库

    在 PooledPostgresqlDatabase 基础上添加了自动重试机制，
    用于处理数据库连接中断等问题。

    Attributes:
        max_retries: 最大重试次数，默认 5 次
        retry_delay: 初始重试延迟（秒），默认 1 秒，后续按指数增长
    """
    def __init__(self, *args, **kwargs):
        """
        初始化带重试机制的PostgreSQL连接池

        Args:
            *args: 位置参数传递给PooledPostgresqlDatabase
            **kwargs: 关键字参数，包括max_retries和retry_delay
        """
        # 从kwargs中提取重试参数
        self.max_retries = kwargs.pop("max_retries", 5)      # 最大重试次数
        self.retry_delay = kwargs.pop("retry_delay", 1)       # 重试延迟
        super().__init__(*args, **kwargs)                     # 调用父类初始化

    def execute_sql(self, sql, params=None, commit=True):
        """
        执行 SQL 语句，支持连接失败自动重试

        Args:
            sql: SQL 语句
            params: SQL 参数
            commit: 是否提交事务

        Returns:
            cursor: 查询结果游标
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)  # 尝试执行SQL
            except (OperationalError, InterfaceError) as e:
                # PostgreSQL特定错误码
                # 57P01: admin_shutdown
                # 57P02: crash_shutdown
                # 57P03: cannot_connect_now
                # 08006: connection_failure
                # 08003: connection_does_not_exist
                # 08000: connection_exception
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                # 判断是否为可重试错误
                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()              # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    logging.error(f"PostgreSQL execution failure: {e}")
                    raise                                          # 重试次数用尽，抛出异常
        return None

    def _handle_connection_loss(self):
        """
        处理连接丢失，尝试重新连接
        """
        try:
            self.close()                                    # 关闭当前连接
        except Exception:
            pass                                        # 忽略关闭异常
        try:
            self.connect()                                   # 尝试重新连接
        except Exception as e:
            logging.error(f"Failed to reconnect to PostgreSQL: {e}")
            time.sleep(0.1)                                # 短暂延迟
            try:
                self.connect()                           # 第二次重试
            except Exception as e2:
                logging.error(f"Failed to reconnect to PostgreSQL on second attempt: {e2}")
                raise                                          # 第二次重试失败，抛出异常

    def begin(self):
        """
        开始事务，支持连接失败自动重试

        Returns:
            transaction: 事务上下文
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()                        # 尝试开始事务
            except (OperationalError, InterfaceError) as e:
                error_messages = ['connection', 'server closed', 'connection refused',
                                'no connection to the server', 'terminating connection']

                # 判断是否为可重试错误
                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"PostgreSQL connection lost during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()              # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    raise                                          # 重试次数用尽，抛出异常
        return None


class RetryingPooledOceanBaseDatabase(PooledMySQLDatabase):
    """
    带重试机制的 OceanBase 连接池数据库

    OceanBase 兼容 MySQL 协议，因此继承自 PooledMySQLDatabase。
    提供连接池和连接失败自动重试功能。

    Attributes:
        max_retries: 最大重试次数，默认 5 次
        retry_delay: 初始重试延迟（秒），默认 1 秒，后续按指数增长
    """
    def __init__(self, *args, **kwargs):
        """
        初始化带重试机制的OceanBase连接池

        Args:
            *args: 位置参数传递给PooledMySQLDatabase
            **kwargs: 关键字参数，包括max_retries和retry_delay
        """
        # 从kwargs中提取重试参数
        self.max_retries = kwargs.pop("max_retries", 5)      # 最大重试次数
        self.retry_delay = kwargs.pop("retry_delay", 1)       # 重试延迟
        super().__init__(*args, **kwargs)                     # 调用父类初始化

    def execute_sql(self, sql, params=None, commit=True):
        """
        执行 SQL 语句，支持连接失败自动重试

        Args:
            sql: SQL 语句
            params: SQL 参数
            commit: 是否提交事务

        Returns:
            cursor: 查询结果游标
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)  # 尝试执行SQL
            except (OperationalError, InterfaceError) as e:
                # OceanBase/MySQL特定错误码
                # 2013: Lost connection to MySQL server during query
                # 2006: MySQL server has gone away
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection', 'gone away']

                # 判断是否为可重试错误
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    any(msg in str(e).lower() for msg in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"OceanBase connection issue (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    self._handle_connection_loss()              # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    logging.error(f"OceanBase execution failure: {e}")
                    raise                                          # 重试次数用尽，抛出异常
        return None

    def _handle_connection_loss(self):
        """
        处理连接丢失，尝试重新连接
        """
        try:
            self.close()                                    # 关闭当前连接
        except Exception:
            pass                                        # 忽略关闭异常
        try:
            self.connect()                                   # 尝试重新连接
        except Exception as e:
            logging.error(f"Failed to reconnect to OceanBase: {e}")
            time.sleep(0.1)                                # 短暂延迟
            try:
                self.connect()                           # 第二次重试
            except Exception as e2:
                logging.error(f"Failed to reconnect to OceanBase on second attempt: {e2}")
                raise                                          # 第二次重试失败，抛出异常

    def begin(self):
        """
        开始事务，支持连接失败自动重试

        Returns:
            transaction: 事务上下文
        """
        # 重试循环
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()                        # 尝试开始事务
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ['', 'Lost connection']

                # 判断是否为可重试错误
                should_retry = (
                    (hasattr(e, 'args') and e.args and e.args[0] in error_codes) or
                    (str(e) in error_messages) or
                    (hasattr(e, '__class__') and e.__class__.__name__ == 'InterfaceError')
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(
                        f"Lost connection during transaction (attempt {attempt+1}/{self.max_retries})"
                    )
                    self._handle_connection_loss()              # 处理连接丢失
                    time.sleep(self.retry_delay * (2 ** attempt))  # 指数退避延迟
                else:
                    raise                                          # 重试次数用尽，抛出异常
        return None


# ==================== 枚举类型定义 ====================

class PooledDatabase(Enum):
    """
    数据库连接池类型枚举

    将数据库类型字符串映射到对应的带重试机制的连接池类。
    """
    MYSQL = RetryingPooledMySQLDatabase              # MySQL连接池
    OCEANBASE = RetryingPooledOceanBaseDatabase      # OceanBase连接池
    POSTGRES = RetryingPooledPostgresqlDatabase      # PostgreSQL连接池


class DatabaseMigrator(Enum):
    """
    数据库迁移器类型枚举

    将数据库类型字符串映射到对应的数据库迁移器类。
    """
    MYSQL = MySQLMigrator                           # MySQL迁移器
    OCEANBASE = MySQLMigrator                         # OceanBase迁移器（使用MySQL迁移器）
    POSTGRES = PostgresqlMigrator                     # PostgreSQL迁移器


# ==================== 单例数据库类 ====================

@singleton                                    # 单例装饰器，确保只有一个实例
class BaseDataBase:
    """
    基础数据库单例类

    使用单例模式管理数据库连接，确保整个应用只有一个数据库连接实例。
    根据配置自动选择对应的数据库类型（MySQL、PostgreSQL 或 OceanBase）。
    """
    def __init__(self):
        """
        初始化数据库连接

        从配置中读取数据库参数，根据数据库类型创建对应的连接池。
        """
        # 从配置中获取数据库参数
        database_config = settings.DATABASE.copy()    # 复制配置避免修改原配置
        db_name = database_config.pop("name")          # 提取数据库名

        # 配置连接池参数
        pool_config = {
            'max_retries': 5,                            # 最大重试次数
            'retry_delay': 1,                            # 重试延迟（秒）
        }
        database_config.update(pool_config)             # 合并连接池配置

        # 根据数据库类型创建对应的连接池
        self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(
            db_name, **database_config
        )
        # self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        logging.info("init database on cluster mode successfully")  # 记录初始化成功


# ==================== 重试装饰器 ====================

def with_retry(max_retries=3, retry_delay=1.0):
    """
    装饰器：为数据库操作添加重试机制

    Args:
        max_retries (int): 最大重试次数，默认 3 次
        retry_delay (float): 初始重试延迟（秒），默认 1 秒，后续按指数增长

    Returns:
        callable: 装饰后的函数

    Usage:
        @with_retry(max_retries=3, retry_delay=1.0)
        def my_function():
            pass
    """

    def decorator(func):
        @wraps(func)                                     # 保留原函数的元信息
        def wrapper(*args, **kwargs):
            last_exception = None
            # 重试循环
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)            # 尝试执行函数
                except Exception as e:
                    last_exception = e                   # 保存异常
                    # 获取self和函数名用于日志
                    self_obj = args[0] if args else None
                    func_name = func.__name__
                    lock_name = getattr(self_obj, "lock_name", "unknown") if self_obj else "unknown"

                    if retry < max_retries - 1:                # 不是最后一次尝试
                        current_delay = retry_delay * (2**retry)  # 指数退避延迟
                        logging.warning(f"{func_name} {lock_name} failed: {str(e)}, retrying ({retry + 1}/{max_retries})")
                        time.sleep(current_delay)                # 等待后重试
                    else:
                        logging.error(f"{func_name} {lock_name} failed after all attempts: {str(e)}")

            # 所有重试都失败，抛出最后一个异常
            if last_exception:
                raise last_exception
            return False                                 # 不应该执行到这里

        return wrapper                                    # 返回包装函数

    return decorator                                         # 返回装饰器


# ==================== 数据库锁实现 ====================

class PostgresDatabaseLock:
    """
    PostgreSQL 数据库锁

    使用 PostgreSQL 的 advisory lock 功能实现分布式锁。
    支持上下文管理器和装饰器两种使用方式。

    Attributes:
        lock_name: 锁名称
        lock_id: 通过 MD5 哈希计算的锁 ID
        timeout: 超时时间（秒）
        db: 数据库连接

    Usage:
        # 上下文管理器方式
        with PostgresDatabaseLock("my_lock", timeout=10):
            # 临界区代码
            pass

        # 装饰器方式
        @PostgresDatabaseLock("my_lock", timeout=10)
        def my_function():
            pass
    """
    def __init__(self, lock_name, timeout=10, db=None):
        """
        初始化PostgreSQL数据库锁

        Args:
            lock_name: 锁名称，用于标识不同的锁
            timeout: 超时时间（秒），默认10秒
            db: 数据库连接，默认使用全局DB
        """
        self.lock_name = lock_name                       # 锁名称
        # 计算锁ID：对锁名称进行MD5哈希后转换为31位整数
        self.lock_id = int(hashlib.md5(lock_name.encode()).hexdigest(), 16) % (2**31 - 1)
        self.timeout = int(timeout)                       # 超时时间
        self.db = db if db else DB                       # 数据库连接，默认使用全局DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        """
        获取锁

        Returns:
            bool: 成功返回 True
        Raises:
            Exception: 获取锁超时或失败
        """
        # 使用PostgreSQL的pg_try_advisory_lock函数尝试获取锁
        cursor = self.db.execute_sql("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:                                # 返回0表示获取失败
            raise Exception(f"acquire postgres lock {self.lock_name} timeout")
        elif ret[0] == 1:                              # 返回1表示获取成功
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        """
        释放锁

        Returns:
            bool: 成功返回 True
        Raises:
            Exception: 释放锁失败
        """
        # 使用PostgreSQL的pg_advisory_unlock函数释放锁
        cursor = self.db.execute_sql("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:                                # 返回0表示锁未被当前线程建立
            raise Exception(f"postgres lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:                              # 返回1表示释放成功
            return True
        else:
            raise Exception(f"postgres lock {self.lock_name} does not exist")

    def __enter__(self):
        """
        上下文管理器入口，获取锁
        """
        # 检查数据库类型是否为PostgreSQL
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.lock()                                   # 获取锁
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        上下文管理器出口，释放锁
        """
        # 检查数据库类型是否为PostgreSQL
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.unlock()                                 # 释放锁

    def __call__(self, func):
        """
        装饰器模式，在函数执行期间持有锁

        Args:
            func: 要装饰的函数

        Returns:
            callable: 装饰后的函数
        """
        @wraps(func)                                     # 保留原函数的元信息
        def magic(*args, **kwargs):
            with self:                                   # 在锁的保护下执行函数
                return func(*args, **kwargs)

        return magic                                       # 返回装饰后的函数


class MysqlDatabaseLock:
    """
    MySQL 数据库锁

    使用 MySQL 的 GET_LOCK/RELEASE_LOCK 函数实现分布式锁。
    支持上下文管理器和装饰器两种使用方式。

    Attributes:
        lock_name: 锁名称
        timeout: 超时时间（秒）
        db: 数据库连接

    Usage:
        # 上下文管理器方式
        with MysqlDatabaseLock("my_lock", timeout=10):
            # 临界区代码
            pass

        # 装饰器方式
        @MysqlDatabaseLock("my_lock", timeout=10)
        def my_function():
            pass
    """
    def __init__(self, lock_name, timeout=10, db=None):
        """
        初始化MySQL数据库锁

        Args:
            lock_name: 锁名称，用于标识不同的锁
            timeout: 超时时间（秒），默认10秒
            db: 数据库连接，默认使用全局DB
        """
        self.lock_name = lock_name                       # 锁名称
        self.timeout = int(timeout)                       # 超时时间
        self.db = db if db else DB                       # 数据库连接，默认使用全局DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        """
        获取锁

        Returns:
            bool: 成功返回 True
        Raises:
            Exception: 获取锁超时或失败
        """
        # SQL参数只支持 %s 格式的占位符
        cursor = self.db.execute_sql("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
        ret = cursor.fetchone()
        if ret[0] == 0:                                # 返回0表示获取失败
            raise Exception(f"acquire mysql lock {self.lock_name} timeout")
        elif ret[0] == 1:                              # 返回1表示获取成功
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        """
        释放锁

        Returns:
            bool: 成功返回 True
        Raises:
            Exception: 释放锁失败
        """
        # 使用RELEASE_LOCK函数释放锁
        cursor = self.db.execute_sql("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
        ret = cursor.fetchone()
        if ret[0] == 0:                                # 返回0表示锁未被当前线程建立
            raise Exception(f"mysql lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:                              # 返回1表示释放成功
            return True
        else:
            raise Exception(f"mysql lock {self.lock_name} does not exist")

    def __enter__(self):
        """
        上下文管理器入口，获取锁
        """
        # 检查数据库类型是否为MySQL（包括PooledMySQLDatabase及其子类）
        if isinstance(self.db, PooledMySQLDatabase):
            self.lock()                                   # 获取锁
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        上下文管理器出口，释放锁
        """
        # 检查数据库类型是否为MySQL
        if isinstance(self.db, PooledMySQLDatabase):
            self.unlock()                                 # 释放锁

    def __call__(self, func):
        """
        装饰器模式，在函数执行期间持有锁

        Args:
            func: 要装饰的函数

        Returns:
            callable: 装饰后的函数
        """
        @wraps(func)                                     # 保留原函数的元信息
        def magic(*args, **kwargs):
            with self:                                   # 在锁的保护下执行函数
                return func(*args, **kwargs)

        return magic                                       # 返回装饰后的函数


# ==================== 数据库锁类型枚举 ====================

class DatabaseLock(Enum):
    """
    数据库锁类型枚举

    将数据库类型字符串映射到对应的数据库锁实现类。
    """
    MYSQL = MysqlDatabaseLock                        # MySQL锁
    OCEANBASE = MysqlDatabaseLock                     # OceanBase锁
    POSTGRES = PostgresDatabaseLock                    # PostgreSQL锁


# ==================== 全局数据库连接 ====================

# 创建全局数据库连接实例
DB = BaseDataBase().database_connection
# 为数据库连接添加锁功能（根据数据库类型选择对应的锁实现）
DB.lock = DatabaseLock[settings.DATABASE_TYPE.upper()].value


# ==================== 连接管理函数 ====================

def close_connection():
    """
    关闭过期的数据库连接

    清理超过指定时间未使用的数据库连接，释放资源。
    使用30秒的过期时间。
    """
    try:
        if DB:                                          # 确保DB存在
            DB.close_stale(age=30)                    # 关闭30秒未使用的连接
    except Exception as e:
        logging.exception(e)                          # 记录异常


# ==================== 数据模型基类 ====================

class DataBaseModel(BaseModel):
    """
    数据库模型基类

    所有数据库表的模型基类，设置数据库连接为全局 DB 实例。
    所有具体的模型类都应该继承此类。
    """
    class Meta:
        database = DB                                 # 设置使用的数据库连接


# ==================== 数据库表初始化 ====================

@DB.connection_context()                          # 使用数据库连接上下文
@DB.lock("init_database_tables", 60)                # 使用分布式锁，60秒超时
def init_database_tables(alter_fields=[]):
    """
    初始化数据库表

    扫描模块中所有继承自 DataBaseModel 的类，自动创建对应的数据库表。
    使用分布式锁确保在多进程/多线程环境下只执行一次。

    Args:
        alter_fields: 需要修改的字段列表（预留参数）

    Raises:
        Exception: 表创建失败时抛出异常
    """
    # 获取模块中所有的类
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    table_objs = []                                # 存储数据库模型类
    create_failed_list = []                         # 记录创建失败的表
    for name, obj in members:
        # 筛选出数据库模型类（排除基类本身）
        if obj != DataBaseModel and issubclass(obj, DataBaseModel):
            table_objs.append(obj)                  # 添加到模型列表

            if not obj.table_exists():              # 表不存在则创建
                logging.debug(f"start create table {obj.__name__}")
                try:
                    obj.create_table(safe=True)     # 安全创建表（幂等）
                    logging.debug(f"create table success: {obj.__name__}")
                except Exception as e:
                    logging.exception(e)              # 记录异常
                    create_failed_list.append(obj.__name__)  # 添加到失败列表
            else:
                logging.debug(f"table {obj.__name__} already exists, skip creation.")

    # 如果有创建失败的表，抛出异常
    if create_failed_list:
        logging.error(f"create tables failed: {create_failed_list}")
        raise Exception(f"create tables failed: {create_failed_list}")
    # 执行数据库迁移
    migrate_db()


# ==================== 数据填充工具函数 ====================

def fill_db_model_object(model_object, human_model_dict):
    """
    用字典数据填充数据库模型对象

    将人类可读格式的字典数据填充到数据库模型对象中。
    只设置模型中存在的字段。

    Args:
        model_object: 数据库模型对象
        human_model_dict: 包含字段值的字典

    Returns:
        DataBaseModel: 填充后的模型对象
    """
    for k, v in human_model_dict.items():           # 遍历字典中的所有键值对
        attr_name = "%s" % k                        # 转换为字符串属性名
        # 只设置模型中存在的字段
        if hasattr(model_object.__class__, attr_name):
            setattr(model_object, attr_name, v)     # 设置属性值
    return model_object                                # 返回填充后的对象


# ==================== 用户模型 ====================

class User(DataBaseModel, AuthUser):
    """
    用户模型

    存储系统用户信息，包括认证信息、个人偏好设置等。

    Attributes:
        id: 用户唯一标识
        access_token: 访问令牌
        nickname: 用户昵称
        password: 密码（加密存储）
        email: 邮箱地址（唯一）
        avatar: 头像（Base64 编码）
        language: 语言偏好（English/Chinese）
        color_schema: 颜色主题（Bright/Dark）
        timezone: 时区设置
        last_login_time: 最后登录时间
        is_authenticated: 是否已认证
        is_active: 是否激活
        is_anonymous: 是否匿名用户
        login_channel: 登录渠道
        status: 状态（0=无效，1=有效）
        is_superuser: 是否超级用户
    """
    # ==================== 主键和认证字段 ====================
    id = CharField(max_length=32, primary_key=True)     # 用户唯一标识
    access_token = CharField(max_length=255, null=True, index=True)  # 访问令牌

    # ==================== 基本信息 ====================
    nickname = CharField(max_length=100, null=False, help_text="nicky name", index=True)  # 昵称
    password = CharField(max_length=255, null=True, help_text="password", index=True)  # 密码
    email = CharField(max_length=255, null=False, help_text="email", unique=True)  # 邮箱（唯一）
    avatar = TextField(null=True, help_text="avatar base64 string")  # 头像（Base64编码）

    # ==================== 偏好设置 ====================
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)  # 语言
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)  # 颜色主题
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)  # 时区

    # ==================== 状态信息 ====================
    last_login_time = DateTimeField(null=True, index=True)  # 最后登录时间
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)  # 是否已认证
    is_active = CharField(max_length=1, null=False, default="1", index=True)          # 是否激活
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)    # 是否匿名用户
    login_channel = CharField(null=True, help_text="from which user login", index=True)  # 登录渠道
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)  # 是否超级用户

    def __str__(self):
        """返回用户的邮箱地址作为字符串表示"""
        return self.email

    def get_id(self):
        """
        获取用户 ID 的 JWT 令牌表示

        使用 itsdangerous 库将 access_token 序列化为带时间戳的 JWT 令牌。

        Returns:
            str: JWT 令牌字符串
        """
        jwt = Serializer(secret_key=settings.SECRET_KEY)    # 创建序列化器
        return jwt.dumps(str(self.access_token))             # 序列化access_token

    class Meta:
        db_table = "user"                               # 数据库表名


# ==================== 租户模型 ====================

class Tenant(DataBaseModel):
    """
    租户模型

    多租户系统的租户信息，每个租户可以有自己的配置和资源配额。

    Attributes:
        id: 租户唯一标识
        name: 租户名称
        public_key: 公钥
        llm_id: 默认 LLM 模型 ID
        tenant_llm_id: 租户 LLM 配置 ID
        embd_id: 默认嵌入模型 ID
        tenant_embd_id: 租户嵌入模型配置 ID
        asr_id: 默认 ASR 模型 ID
        tenant_asr_id: 租户 ASR 模型配置 ID
        img2txt_id: 默认图像转文本模型 ID
        tenant_img2txt_id: 租户图像转文本模型配置 ID
        rerank_id: 默认重排序模型 ID
        tenant_rerank_id: 租户重排序模型配置 ID
        tts_id: 默认 TTS 模型 ID
        tenant_tts_id: 租户 TTS 模型配置 ID
        parser_ids: 文档解析器列表
        credit: 租户配额
        status: 状态（0=无效，1=有效）
    """
    # ==================== 主键和基本信息 ====================
    id = CharField(max_length=32, primary_key=True)           # 租户唯一标识
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)  # 租户名称
    public_key = CharField(max_length=255, null=True, index=True)  # 公钥

    # ==================== LLM 配置 ====================
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID", index=True)  # 默认LLM模型ID
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户LLM配置ID

    # ==================== Embedding 配置 ====================
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)  # 默认嵌入模型ID
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户嵌入模型配置ID

    # ==================== ASR 配置 ====================
    asr_id = CharField(max_length=128, null=False, help_text="default ASR model ID", index=True)  # 默认ASR模型ID
    tenant_asr_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户ASR模型配置ID

    # ==================== 图像转文本配置 ====================
    img2txt_id = CharField(max_length=128, null=False, help_text="default image to text model ID", index=True)  # 默认图像转文本模型ID
    tenant_img2txt_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户图像转文本模型配置ID

    # ==================== Rerank 配置 ====================
    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID")  # 默认重排序模型ID
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户重排序模型配置ID

    # ==================== TTS 配置 ====================
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)  # 默认TTS模型ID
    tenant_tts_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)  # 租户TTS模型配置ID

    # ==================== 其他配置 ====================
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)  # 文档解析器列表
    credit = IntegerField(default=512, index=True)             # 租户配额
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    class Meta:
        db_table = "tenant"                              # 数据库表名


# ==================== 用户-租户关联模型 ====================

class UserTenant(DataBaseModel):
    """
    用户-租户关联模型

    多对多关系表，记录用户与租户的关联关系及用户角色。

    Attributes:
        id: 关联记录唯一标识
        user_id: 用户 ID
        tenant_id: 租户 ID
        role: 用户角色（UserTenantRole）
        invited_by: 邀请人 ID
        status: 状态（0=无效，1=有效）
    """
    # ==================== 主键 ====================
    id = CharField(max_length=32, primary_key=True)        # 关联记录唯一标识

    # ==================== 关联字段 ====================
    user_id = CharField(max_length=32, null=False, index=True)       # 用户ID
    tenant_id = CharField(max_length=32, null=False, index=True)     # 租户ID
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)  # 用户角色
    invited_by = CharField(max_length=32, null=False, index=True)   # 邀请人ID
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    class Meta:
        db_table = "user_tenant"                        # 数据库表名


# ==================== 邀请码模型 ====================

class InvitationCode(DataBaseModel):
    """
    邀请码模型

    用于管理用户加入租户的邀请码。

    Attributes:
        id: 邀请码唯一标识
        code: 邀请码
        visit_time: 访问时间
        user_id: 使用邀请码的用户 ID
        tenant_id: 目标租户 ID
        status: 状态（0=无效，1=有效）
    """
    # ==================== 主键 ====================
    id = CharField(max_length=32, primary_key=True)        # 邀请码唯一标识
    code = CharField(max_length=32, null=False, index=True)        # 邀请码
    visit_time = DateTimeField(null=True, index=True)            # 访问时间
    user_id = CharField(max_length=32, null=True, index=True)    # 使用邀请码的用户ID
    tenant_id = CharField(max_length=32, null=True, index=True)   # 目标租户ID
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    class Meta:
        db_table = "invitation_code"                     # 数据库表名


# ==================== LLM 厂商模型 ====================

class LLMFactories(DataBaseModel):
    """
    LLM 厂商模型

    存储支持的大语言模型厂商信息。

    Attributes:
        name: 厂商名称（主键）
        logo: 厂商 Logo（Base64 编码）
        tags: 标签（LLM, Text Embedding, Image2Text, ASR）
        rank: 排序权重
        status: 状态（0=无效，1=有效）
    """
    # ==================== 主键 ====================
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)  # 厂商名称（主键）
    logo = TextField(null=True, help_text="llm logo base64")  # 厂商Logo（Base64编码）
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)  # 标签
    rank = IntegerField(default=0, index=False)             # 排序权重
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    def __str__(self):
        """返回厂商名称"""
        return self.name

    class Meta:
        db_table = "llm_factories"                       # 数据库表名


# ==================== LLM 模型 ====================

class LLM(DataBaseModel):
    """
    大语言模型模型

    存储系统支持的各类 AI 模型信息。

    Attributes:
        llm_name: 模型名称
        model_type: 模型类型（LLM, Text Embedding, Image2Text, ASR）
        fid: 厂商 ID
        max_tokens: 最大 token 数
        tags: 标签
        is_tools: 是否支持工具调用
        status: 状态（0=无效，1=有效）
    """
    # LLMs dictionary
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)  # 模型名称
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)  # 模型类型
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)  # 厂商ID
    max_tokens = IntegerField(default=0)                 # 最大token数

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)  # 标签
    is_tools = BooleanField(null=False, help_text="support tools", default=False)  # 是否支持工具调用
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    def __str__(self):
        """返回模型名称"""
        return self.llm_name

    class Meta:
        # 复合主键：厂商 + 模型名称
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"                                # 数据库表名


# ==================== 租户 LLM 配置模型 ====================

class TenantLLM(DataBaseModel):
    """
    租户 LLM 配置模型

    存储租户自定义的 LLM 配置，包括 API 密钥等。

    Attributes:
        id: 配置记录 ID（主键）
        tenant_id: 租户 ID
        llm_factory: LLM 厂商名称
        model_type: 模型类型
        llm_name: 模型名称
        api_key: API 密钥
        api_base: API 基础 URL
        max_tokens: 最大上下文 token 数
        used_tokens: 已使用 token 数
        status: 状态（0=无效，1=有效）
    """
    # ==================== 主键 ====================
    id = PrimaryKeyField()                            # 配置记录ID（自增主键）

    # ==================== 租户关联 ====================
    tenant_id = CharField(max_length=32, null=False, index=True)  # 租户ID

    # ==================== 模型信息 ====================
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)  # LLM厂商名称
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)  # 模型类型
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)  # 模型名称

    # ==================== API 配置 ====================
    api_key = TextField(null=True, help_text="API KEY")  # API密钥
    api_base = CharField(max_length=255, null=True, help_text="API Base")  # API基础URL

    # ==================== 使用限制 ====================
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)  # 最大上下文token数
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)  # 已使用token数

    # ==================== 状态 ====================
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)  # 状态

    def __str__(self):
        """返回模型名称"""
        return self.llm_name

    class Meta:
        db_table = "tenant_llm"                           # 数据库表名
        # 唯一索引：租户 + 厂商 + 模型名称
        indexes = (
            (("tenant_id", "llm_factory", "llm_name"), True),
        )


# ==================== 租户 Langfuse 配置模型 ====================

class TenantLangfuse(DataBaseModel):
    """
    租户 Langfuse 配置模型

    存储 Langfuse（LLM观测平台）的配置信息。

    Attributes:
        tenant_id: 租户 ID（主键）
        secret_key: 密钥
        public_key: 公钥
        host: 主机地址
    """
    # ==================== 主键 ====================
    tenant_id = CharField(max_length=32, null=False, primary_key=True)  # 租户ID（主键）

    # ==================== Langfuse 配置 ====================
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY", index=True)   # 密钥
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY", index=True)  # 公钥
    host = CharField(max_length=128, null=False, help_text="HOST", index=True)               # 主机地址

    def __str__(self):
        """返回Langfuse主机地址"""
        return "Langfuse host" + self.host

    class Meta:
        db_table = "tenant_langfuse"                      # 数据库表名
