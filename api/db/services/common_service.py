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
通用服务模块

本模块提供所有服务类的基类，定义了通用的数据库操作方法。

主要功能：
- 基础 CRUD 操作（创建、读取、更新、删除）
- 数据库连接重试机制（死锁重试、连接错误重试）
- 批量操作支持
- 通用查询方法

核心类：
- CommonService: 所有服务类的基类
"""
import logging
import time
from datetime import datetime
from functools import wraps

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import peewee
from peewee import InterfaceError, OperationalError

from api.db.db_models import DB
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format


def _is_deadlock_error(exc: OperationalError) -> bool:
    """
    判断异常是否为数据库死锁错误

    :param exc: 数据库操作异常
    :return: 是否为死锁错误（错误码 1213）
    """
    return isinstance(exc, OperationalError) and bool(getattr(exc, "args", ())) and exc.args[0] == 1213


def retry_deadlock_operation(max_retries=3, retry_delay=0.1):
    """
    数据库死锁重试装饰器

    当 MySQL/OceanBase 因死锁中止操作时，自动重试整个操作。

    :param max_retries: 最大重试次数
    :param retry_delay: 初始重试延迟（秒）
    :return: 装饰器函数
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    if not _is_deadlock_error(e) or attempt >= max_retries - 1:
                        raise
                    current_delay = retry_delay * (2**attempt)
                    logging.warning(
                        "%s failed due to DB deadlock, retrying (%s/%s): %s",
                        func.__qualname__,
                        attempt + 1,
                        max_retries,
                        e,
                    )
                    time.sleep(current_delay)

        return wrapper

    return decorator


def retry_db_operation(func):
    """
    数据库操作重试装饰器（使用 tenacity）

    对连接错误和操作错误进行自动重试，最多重试 3 次。

    :param func: 要包装的函数
    :return: 包装后的函数
    """
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((InterfaceError, OperationalError)),
        before_sleep=lambda retry_state: print(f"RETRY {retry_state.attempt_number} TIMES"),
        reraise=True,
    )
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


class CommonService:
    """
    通用服务基类

    为所有服务类提供基础的数据库操作方法，实现标准的 CRUD 操作
    和通用数据库查询模式。使用 Peewee ORM 进行数据库交互，
    为所有派生服务类提供一致的数据库操作接口。

    Attributes:
        model: 此服务操作的 Peewee 模型类（必须由子类设置）
    """

    model = None

    @classmethod
    @DB.connection_context()
    def query(cls, cols=None, reverse=None, order_by=None, **kwargs):
        """执行数据库查询，支持可选的列选择和排序

        此方法提供了一种灵活的方式来查询数据库，支持各种过滤条件和排序选项。
        它委托给模型类的 query 方法实现具体的查询逻辑。

        逻辑说明：
        1. 直接调用模型类的 query 方法，透传所有参数
        2. 使用 @DB.connection_context() 装饰器确保在数据库连接上下文中执行

        Args:
            cols (list, optional): 要选择的列名列表。如果为 None，则选择所有列
            reverse (bool, optional): 如果为 True，降序排序；如果为 False，升序排序
            order_by (str, optional): 排序依据的列名
            **kwargs: 作为关键字参数传递的额外过滤条件

        Returns:
            peewee.ModelSelect: 包含匹配记录的查询结果
        """
        return cls.model.query(cols=cols, reverse=reverse, order_by=order_by, **kwargs)

    @classmethod
    @DB.connection_context()
    def get_all(cls, cols=None, reverse=None, order_by=None):
        """获取数据库中的所有记录，支持可选的列选择和排序

        此方法从模型对应的表中获取所有记录，支持列选择和结果排序。
        如果未指定 order_by 且 reverse 为 True，则默认按 create_time 排序。

        逻辑说明：
        1. 根据 cols 参数选择特定列或所有列
        2. 如果指定了 reverse 参数，则进行排序处理
        3. 如果 order_by 未指定或指定的列不存在，默认使用 create_time
        4. 使用 getter_by 方法获取排序字段，支持动态字段访问

        Args:
            cols (list, optional): 要选择的列名列表。如果为 None，则选择所有列
            reverse (bool, optional): 如果为 True，降序排序；如果为 False，升序排序
            order_by (str, optional): 排序依据的列名。如果指定了 reverse 但未指定此参数，默认为 'create_time'

        Returns:
            peewee.ModelSelect: 包含所有匹配记录的查询对象
        """
        if cols:
            query_records = cls.model.select(*cols)
        else:
            query_records = cls.model.select()
        if reverse is not None:
            if not order_by or not hasattr(cls.model, order_by):
                order_by = "create_time"
            if reverse is True:
                query_records = query_records.order_by(cls.model.getter_by(order_by).desc())
            elif reverse is False:
                query_records = query_records.order_by(cls.model.getter_by(order_by).asc())
        return query_records

    @classmethod
    @DB.connection_context()
    def get(cls, **kwargs):
        """获取符合给定条件的单条记录

        此方法从数据库中检索与指定过滤条件匹配的单条记录。
        如果找不到匹配的记录，将抛出 DoesNotExist 异常。

        逻辑说明：
        1. 直接调用 Peewee 模型的 get 方法
        2. 将关键字参数作为过滤条件传递
        3. 期望只返回一条记录，如果有多条会报错

        Args:
            **kwargs: 作为关键字参数的过滤条件

        Returns:
            Model instance: 单条匹配记录的模型实例

        Raises:
            peewee.DoesNotExist: 如果没有找到匹配的记录
            peewee.IntegrityError: 如果找到多条匹配记录
        """
        return cls.model.get(**kwargs)

    @classmethod
    @DB.connection_context()
    def get_or_none(cls, **kwargs):
        """获取单条记录，如果未找到则返回 None

        此方法尝试检索与给定条件匹配的单条记录。
        如果没有找到匹配项，返回 None 而不是抛出异常。
        这是一个更安全的查询方法，适合用于不确定记录是否存在的场景。

        逻辑说明：
        1. 使用 try-except 捕获 DoesNotExist 异常
        2. 如果找到记录，返回模型实例
        3. 如果未找到记录，返回 None 而非抛出异常

        Args:
            **kwargs: 作为关键字参数的过滤条件

        Returns:
            Model instance or None: 如果找到则返回匹配记录，否则返回 None
        """
        try:
            return cls.model.get(**kwargs)
        except peewee.DoesNotExist:
            return None

    @classmethod
    @DB.connection_context()
    def save(cls, **kwargs):
        """保存新记录到数据库

        此方法使用提供的字段值在数据库中创建新记录。
        通过 force_insert=True 强制执行插入操作，而不是更新操作。
        注意：此方法不会自动生成 ID 和时间戳，需要手动提供。

        逻辑说明：
        1. 使用 kwargs 创建模型实例
        2. 调用 save 方法时指定 force_insert=True，强制执行 INSERT
        3. 返回保存后的模型实例

        Args:
            **kwargs: 作为关键字参数的记录字段值

        Returns:
            Model instance: 创建的记录对象

        Note:
            此方法不会自动生成 id、create_time 等字段。
            如需自动生成这些字段，请使用 insert() 方法。
        """
        sample_obj = cls.model(**kwargs).save(force_insert=True)
        return sample_obj

    @classmethod
    @DB.connection_context()
    def insert(cls, **kwargs):
        """插入新记录，自动生成 ID 和时间戳

        此方法创建一个新记录，并自动生成 ID 和时间戳字段。
        相比 save() 方法，此方法会自动处理以下字段的生成：
        - id: 使用 UUID 自动生成
        - create_time: 当前时间戳
        - create_date: 当前日期时间
        - update_time: 当前时间戳（与 create_time 相同）
        - update_date: 当前日期时间（与 create_date 相同）

        逻辑说明：
        1. 检查 kwargs 中是否包含 id，如果不包含则自动生成 UUID
        2. 获取当前时间戳和格式化后的日期时间
        3. 将时间字段添加到 kwargs 中
        4. 使用 force_insert=True 强制执行插入操作

        Args:
            **kwargs: 作为关键字参数的记录字段值

        Returns:
            Model instance: 新创建的记录对象
        """
        if "id" not in kwargs:
            kwargs["id"] = get_uuid()
        timestamp = current_timestamp()
        cur_datetime = datetime_format(datetime.now())
        kwargs["create_time"] = timestamp
        kwargs["create_date"] = cur_datetime
        kwargs["update_time"] = timestamp
        kwargs["update_date"] = cur_datetime
        sample_obj = cls.model(**kwargs).save(force_insert=True)
        return sample_obj

    @classmethod
    @DB.connection_context()
    def insert_many(cls, data_list, batch_size=100):
        """批量插入多条记录

        此方法使用批处理高效地将多条记录插入数据库。
        它会自动为所有记录设置创建时间戳。
        使用事务（atomic）确保所有记录要么全部成功，要么全部失败。

        逻辑说明：
        1. 获取当前时间戳和日期时间（所有记录使用相同的时间）
        2. 使用 DB.atomic() 开启事务，确保数据一致性
        3. 遍历数据列表，为每条记录添加时间戳字段
        4. 按批次大小分批执行插入操作，提高性能
        5. 默认每批插入 100 条记录，可通过 batch_size 参数调整

        Args:
            data_list (list): 要插入的记录数据字典列表
            batch_size (int, optional): 每批插入的记录数，默认为 100

        Note:
            - 所有记录使用相同的创建时间戳（当前时间）
            - 使用事务确保操作的原子性
            - 批量插入比逐条插入性能更好
        """
        current_ts = current_timestamp()
        current_datetime = datetime_format(datetime.now())
        with DB.atomic():
            for d in data_list:
                d["create_time"] = current_ts
                d["create_date"] = current_datetime
                d["update_time"] = current_ts
                d["update_date"] = current_datetime

            for i in range(0, len(data_list), batch_size):
                cls.model.insert_many(data_list[i : i + batch_size]).execute()

    @classmethod
    @DB.connection_context()
    def update_many_by_id(cls, data_list):
        """根据 ID 批量更新多条记录

        此方法更新数据库中的多条记录，通过它们的 ID 进行标识。
        它会自动更新每条记录的 update_time 和 update_date 字段。
        使用事务确保所有更新操作要么全部成功，要么全部失败。

        逻辑说明：
        1. 获取当前时间戳和日期时间
        2. 遍历数据列表，为每条记录添加更新时间戳
        3. 使用 DB.atomic() 开启事务
        4. 逐条执行更新操作，根据 ID 匹配记录
        5. 每个字典必须包含 'id' 字段用于标识要更新的记录

        Args:
            data_list (list): 要更新的记录数据字典列表，每个字典必须包含 'id' 字段

        Note:
            - 所有记录使用相同的更新时间戳（当前时间）
            - 使用事务确保操作的原子性
            - 每条记录根据其 id 字段进行匹配和更新
        """
        timestamp = current_timestamp()
        cur_datetime = datetime_format(datetime.now())
        for data in data_list:
            data["update_time"] = timestamp
            data["update_date"] = cur_datetime
        with DB.atomic():
            for data in data_list:
                cls.model.update(data).where(cls.model.id == data["id"]).execute()

    @classmethod
    @DB.connection_context()
    @retry_db_operation
    def update_by_id(cls, pid, data):
        """根据 ID 更新单条记录，自动更新时间戳

        此方法更新由 ID 标识的特定记录。
        它会自动更新 update_time 和 update_date 字段以反映修改时间。
        该方法包含自动重试逻辑，用于处理瞬态数据库错误（如连接断开）。

        逻辑说明：
        1. 使用 @retry_db_operation 装饰器实现自动重试机制
        2. 获取当前时间戳并更新到 data 字典中
        3. 构建 UPDATE 语句，WHERE 条件为 id 匹配
        4. 执行更新操作并返回受影响的行数

        Args:
            pid (str): 要更新的记录的唯一标识符
            data (dict): 包含字段名和新值的字典

        Returns:
            int: 受更新操作影响的记录数（通常为 1，如果记录不存在则为 0）

        Examples:
            >>> service.update_by_id("user-123", {"status": "active", "score": 95})
            1

        Note:
            - 使用了重试装饰器，遇到连接错误会自动重试最多 3 次
            - update_time 和 update_date 会自动设置为当前时间
        """
        data["update_time"] = current_timestamp()
        data["update_date"] = datetime_format(datetime.now())
        num = cls.model.update(data).where(cls.model.id == pid).execute()
        return num

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, pid):
        """根据 ID 获取记录，带错误处理

        此方法根据唯一标识符检索单条记录。
        与标准的 get 方法不同，此方法返回一个表示成功/失败的元组，
        而不是在找不到记录时抛出异常。
        这种设计模式使调用方能够更优雅地处理记录不存在的情况。

        逻辑说明：
        1. 使用 get_or_none 方法尝试获取记录（避免抛出 DoesNotExist 异常）
        2. 如果找到记录，返回 (True, 记录对象)
        3. 如果未找到或发生异常，返回 (False, None)
        4. 使用 try-except 捕获所有可能的异常

        Args:
            pid (str): 要检索的记录的唯一标识符

        Returns:
            tuple: 包含两个元素的元组：
                - bool: 如果找到记录则为 True，否则为 False
                - Model instance or None: 如果找到则返回记录对象，否则返回 None

        Examples:
            >>> found, user = service.get_by_id("user-123")
            >>> if found:
            ...     print(f"Found user: {user.name}")
            ... else:
            ...     print("User not found")

        Note:
            此方法比 get() 更安全，因为它不会抛出异常，
            适合用于需要检查记录是否存在的场景。
        """
        try:
            obj = cls.model.get_or_none(cls.model.id == pid)
            if obj:
                return True, obj
        except Exception:
            pass
        return False, None

    @classmethod
    @DB.connection_context()
    def get_by_ids(cls, pids, cols=None):
        """根据多个 ID 获取记录

        此方法根据 ID 列表检索多条记录，支持列选择。
        使用 SQL 的 IN 子句实现批量查询，比逐条查询更高效。

        逻辑说明：
        1. 根据 cols 参数决定选择特定列或所有列
        2. 使用 select().where(id.in_(pids)) 构建 IN 查询
        3. 返回查询对象，可以进一步迭代或转换为列表

        Args:
            pids (list): 记录 ID 列表
            cols (list, optional): 要选择的列名列表，如果为 None 则选择所有列

        Returns:
            peewee.ModelSelect: 匹配记录的查询对象

        Examples:
            >>> users = service.get_by_ids(["user-1", "user-2", "user-3"])
            >>> for user in users:
            ...     print(user.name)

        Note:
            - 使用 IN 子句一次性查询所有记录，性能优于循环查询
            - 返回的是查询对象，不是列表，需要时可以转换为 list
        """
        if cols:
            objs = cls.model.select(*cols)
        else:
            objs = cls.model.select()
        return objs.where(cls.model.id.in_(pids))

    @classmethod
    @DB.connection_context()
    def delete_by_id(cls, pid):
        """根据 ID 删除单条记录

        此方法根据唯一标识符删除单条记录。

        逻辑说明：
        1. 构建 DELETE 语句，WHERE 条件为 id 匹配
        2. 执行删除操作
        3. 返回受影响的行数（1 表示成功删除，0 表示记录不存在）

        Args:
            pid (str): 要删除的记录 ID

        Returns:
            int: 删除的记录数（1 或 0）

        Examples:
            >>> num = service.delete_by_id("user-123")
            >>> print(f"Deleted {num} record(s)")
        """
        return cls.model.delete().where(cls.model.id == pid).execute()

    @classmethod
    @DB.connection_context()
    def delete_by_ids(cls, pids):
        """根据多个 ID 批量删除记录

        此方法根据 ID 列表批量删除多条记录。
        使用事务确保所有删除操作要么全部成功，要么全部失败。

        逻辑说明：
        1. 使用 DB.atomic() 开启事务，确保操作的原子性
        2. 构建 DELETE 语句，使用 IN 子句匹配多个 ID
        3. 执行删除操作并返回受影响的行数

        Args:
            pids (list): 要删除的记录 ID 列表

        Returns:
            int: 删除的记录数

        Examples:
            >>> num = service.delete_by_ids(["user-1", "user-2", "user-3"])
            >>> print(f"Deleted {num} record(s)")

        Note:
            - 使用事务确保操作的原子性
            - 使用 IN 子句一次性删除所有匹配的记录
        """
        with DB.atomic():
            res = cls.model.delete().where(cls.model.id.in_(pids)).execute()
            return res

    @classmethod
    @DB.connection_context()
    def filter_delete(cls, filters):
        """根据过滤条件删除记录

        此方法根据提供的过滤条件删除匹配的记录。
        支持复杂的 WHERE 条件组合，使用事务确保操作的原子性。

        逻辑说明：
        1. 使用 DB.atomic() 开启事务
        2. 构建 DELETE 语句，WHERE 条件由 filters 参数指定
        3. 使用 *filters 展开过滤条件列表
        4. 执行删除操作并返回受影响的行数

        Args:
            filters (list): 过滤条件列表，每个条件都是 Peewee 表达式

        Returns:
            int: 删除的记录数

        Examples:
            >>> from peewee import Q
            >>> filters = [Q(status='inactive'), Q(last_login__lt=datetime.now())]
            >>> num = service.filter_delete(filters)

        Note:
            - 使用事务确保操作的原子性
            - 多个过滤条件之间是 AND 关系
            - 使用 Q 对象可以构建复杂的查询条件
        """
        with DB.atomic():
            num = cls.model.delete().where(*filters).execute()
            return num

    @classmethod
    @DB.connection_context()
    def filter_update(cls, filters, update_data):
        """根据过滤条件更新记录

        此方法根据提供的过滤条件更新匹配的记录。
        支持复杂的 WHERE 条件组合，使用事务确保操作的原子性。

        逻辑说明：
        1. 使用 DB.atomic() 开启事务
        2. 构建 UPDATE 语句，SET 子句由 update_data 指定
        3. WHERE 条件由 filters 参数指定，使用 *filters 展开
        4. 执行更新操作并返回受影响的行数

        Args:
            filters (list): 过滤条件列表，每个条件都是 Peewee 表达式
            update_data (dict): 要更新的字段和值的字典

        Returns:
            int: 更新的记录数

        Examples:
            >>> from peewee import Q
            >>> filters = [Q(status='pending')]
            >>> update_data = {'status': 'processed', 'processed_at': datetime.now()}
            >>> num = service.filter_update(filters, update_data)

        Note:
            - 使用事务确保操作的原子性
            - 多个过滤条件之间是 AND 关系
            - 不会自动更新 update_time 字段，如需更新请手动添加
        """
        with DB.atomic():
            return cls.model.update(update_data).where(*filters).execute()

    @staticmethod
    def cut_list(tar_list, n):
        """将列表分割成指定大小的块

        此静态方法将一个列表分割成多个大小为 n 的块。
        主要用于处理需要分批操作的场景，如批量查询或批量更新。

        逻辑说明：
        1. 获取列表长度
        2. 创建索引范围
        3. 使用列表推导式，以 n 为步长遍历索引
        4. 对每个步长位置，提取 n 个元素并转换为元组

        Args:
            tar_list (list): 要分割的目标列表
            n (int): 每个块的大小

        Returns:
            list: 包含元组的列表，每个元组包含最多 n 个元素

        Examples:
            >>> CommonService.cut_list([1, 2, 3, 4, 5, 6, 7], 3)
            [(1, 2, 3), (4, 5, 6), (7,)]

        Note:
            - 最后一个块可能小于 n 个元素
            - 返回的是元组列表，不是列表的列表
            - 这是一个静态方法，可以直接通过类调用
        """
        length = len(tar_list)
        arr = range(length)
        result = [tuple(tar_list[x : (x + n)]) for x in arr[::n]]
        return result

    @classmethod
    @DB.connection_context()
    def filter_scope_list(cls, in_key, in_filters_list, filters=None, cols=None):
        """使用 IN 子句过滤并获取记录列表

        此方法通过 IN 子句过滤记录，支持额外的过滤条件和列选择。
        为了避免 SQL IN 子句过长，会自动将过滤列表分批处理（每批 20 个）。

        逻辑说明：
        1. 使用 cut_list 方法将过滤列表分割成每批 20 个元素的元组
        2. 初始化 filters 为空列表（如果为 None）
        3. 根据 cols 参数决定选择特定列或所有列
        4. 遍历每个批次，执行查询并累加结果
        5. 使用 getattr 动态获取模型字段，支持任意字段名
        6. 将所有批次的结果合并到一个列表中返回

        Args:
            in_key (str): 用于 IN 子句的字段名
            in_filters_list (list): IN 子句的值列表
            filters (list, optional): 额外的过滤条件，默认为 None
            cols (list, optional): 要选择的列名列表，默认为 None（选择所有列）

        Returns:
            list: 匹配记录的列表

        Examples:
            >>> # 获取特定知识库的文档
            >>> docs = service.filter_scope_list(
            ...     in_key="kb_id",
            ...     in_filters_list=["kb-1", "kb-2", "kb-3"],
            ...     filters=[Q(status='active')],
            ...     cols=["id", "name"]
            ... )

        Note:
            - 自动分批处理，每批最多 20 个值，避免 SQL 语句过长
            - 多个批次的查询结果会合并到同一个列表中
            - 适合处理大量 ID 的 IN 查询场景
        """
        in_filters_tuple_list = cls.cut_list(in_filters_list, 20)
        if not filters:
            filters = []
        res_list = []
        if cols:
            for i in in_filters_tuple_list:
                query_records = cls.model.select(*cols).where(getattr(cls.model, in_key).in_(i), *filters)
                if query_records:
                    res_list.extend([query_record for query_record in query_records])
        else:
            for i in in_filters_tuple_list:
                query_records = cls.model.select().where(getattr(cls.model, in_key).in_(i), *filters)
                if query_records:
                    res_list.extend([query_record for query_record in query_records])
        return res_list
