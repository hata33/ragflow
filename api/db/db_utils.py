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
数据库工具模块

本模块提供数据库操作的通用工具函数，包括：
- 批量插入数据（支持冲突处理）
- 动态数据库模型生成
- 查询字典转换为数据库表达式
- 数据库查询辅助函数
"""
import operator
from functools import reduce

from playhouse.pool import PooledMySQLDatabase

from common.time_utils import current_timestamp, timestamp_to_date

from api.db.db_models import DB, DataBaseModel


@DB.connection_context()
def bulk_insert_into_db(model, data_source, replace_on_conflict=False):
    """
    批量插入数据到数据库

    自动为每条记录添加创建时间和更新时间戳。
    支持冲突时替换（MySQL）或忽略（PostgreSQL）。

    逻辑说明：
    1. 确保表存在
    2. 为每条记录添加时间戳（递增以确保唯一性）
    3. 计算需要保留的字段（排除自动生成的时间字段）
    4. 分批插入数据（每批 1000 条）
    5. 根据数据库类型处理冲突

    :param model: 数据库模型类
    :param data_source: 数据字典列表
    :param replace_on_conflict: 是否在冲突时替换
    """
    # 确保模型对应的表已创建（如果不存在则创建）
    DB.create_tables([model])

    # 为每条数据添加时间戳字段
    # 使用递增的时间戳确保每条记录的创建时间略有不同，便于排序和追踪
    for i, data in enumerate(data_source):
        # 计算当前记录的时间戳（基础时间 + 索引偏移）
        current_time = current_timestamp() + i
        current_date = timestamp_to_date(current_time)

        # 如果数据中没有 create_time，则使用当前时间
        if 'create_time' not in data:
            data['create_time'] = current_time

        # 根据 create_time 计算对应的日期
        data['create_date'] = timestamp_to_date(data['create_time'])

        # 设置更新时间和日期（与创建时间相同）
        data['update_time'] = current_time
        data['update_date'] = current_date

    # 计算需要保留的字段（在冲突时保留这些字段的值）
    # 排除 create_time 和 create_date，因为它们在冲突时应该保持原值
    preserve = tuple(data_source[0].keys() - {'create_time', 'create_date'})

    # 设置批量插入的大小（每批 1000 条记录）
    # 批量插入可以提高性能，同时避免单次操作数据量过大
    batch_size = 1000

    # 分批插入数据
    for i in range(0, len(data_source), batch_size):
        # 使用事务确保批量插入的原子性
        # 如果中途失败，整个批次都会回滚
        with DB.atomic():
            # 构建批量插入查询
            query = model.insert_many(data_source[i:i + batch_size])

            # 如果需要处理冲突（即主键或唯一索引重复）
            if replace_on_conflict:
                # 根据数据库类型选择不同的冲突处理策略
                if isinstance(DB, PooledMySQLDatabase):
                    # MySQL: 使用 ON DUPLICATE KEY UPDATE
                    # preserve 参数指定需要更新的字段
                    query = query.on_conflict(preserve=preserve)
                else:
                    # PostgreSQL: 使用 ON CONFLICT ... DO UPDATE
                    # conflict_target 指定冲突检测的目标字段（通常是主键 id）
                    query = query.on_conflict(conflict_target="id", preserve=preserve)

            # 执行插入操作
            query.execute()


def get_dynamic_db_model(base, job_id):
    """
    获取动态数据库模型

    根据任务 ID 生成对应的数据库模型类，支持分表存储。

    逻辑说明：
    1. 从 job_id 提取表索引（前 8 位）
    2. 使用 type 动态创建模型类
    3. 返回动态生成的模型类

    :param base: 基础模型类
    :param job_id: 任务 ID
    :return: 动态生成的模型类
    """
    # 使用 type 函数动态创建类
    # base.model() 返回模型的元类信息
    # table_index 指定使用哪个分表
    return type(base.model(
        table_index=get_dynamic_tracking_table_index(job_id=job_id)))


def get_dynamic_tracking_table_index(job_id):
    """
    获取动态追踪表的索引

    从任务 ID 中提取前 8 位作为表索引，用于分表存储。

    逻辑说明：
    1. 提取 job_id 的前 8 个字符
    2. 这 8 位字符作为表的后缀，实现数据分片

    :param job_id: 任务 ID
    :return: 表索引（job_id 的前 8 位）

    示例：
        job_id = "abc12345def67890" -> 返回 "abc12345"
    """
    # 截取 job_id 的前 8 位作为表索引
    return job_id[:8]


def fill_db_model_object(model_object, human_model_dict):
    """
    用字典数据填充数据库模型对象

    将人类可读的字典（字段名无前缀）转换为数据库模型对象（字段名有 f_ 前缀）。

    逻辑说明：
    1. 遍历输入字典的每个键值对
    2. 为字段名添加 f_ 前缀（数据库字段命名规范）
    3. 检查模型是否有该字段
    4. 如果有，则设置对应的值

    :param model_object: 数据库模型对象（会被就地修改）
    :param human_model_dict: 人类可读的字典（无 f_ 前缀）
    :return: 填充后的模型对象

    示例：
        human_model_dict = {"name": "test", "status": "active"}
        -> 设置 model_object.f_name = "test", f_status = "active"
    """
    # 遍历字典中的每个字段
    for k, v in human_model_dict.items():
        # 为字段名添加 f_ 前缀，匹配数据库字段的命名规范
        attr_name = 'f_%s' % k

        # 检查模型类是否有该属性（避免设置不存在的字段）
        if hasattr(model_object.__class__, attr_name):
            # 设置模型对象的属性值
            setattr(model_object, attr_name, v)

    # 返回填充后的模型对象（支持链式调用）
    return model_object


# Peewee ORM 支持的查询操作符映射表
# https://docs.peewee-orm.com/en/latest/peewee/query_operators.html
# 用于将字符串操作符转换为 Python 的 operator 模块函数
supported_operators = {
    '==': operator.eq,      # 等于
    '<': operator.lt,       # 小于
    '<=': operator.le,      # 小于等于
    '>': operator.gt,       # 大于
    '>=': operator.ge,      # 大于等于
    '!=': operator.ne,      # 不等于
    '<<': operator.lshift,  # 左移位
    '>>': operator.rshift,  # 右移位
    '%': operator.mod,      # 取模
    '**': operator.pow,     # 幂运算
    '^': operator.xor,      # 异或
    '~': operator.inv,      # 按位取反
}


def query_dict2expression(
        model: type[DataBaseModel], query: dict[str, bool | int | str | list | tuple]):
    """
    将查询字典转换为 Peewee 表达式

    将人类可读的查询条件字典转换为 Peewee ORM 可以执行的数据库表达式。

    逻辑说明：
    1. 遍历查询字典的每个字段
    2. 如果值不是列表/元组，默认使用 == 操作符
    3. 提取操作符和操作数
    4. 为字段名添加 f_ 前缀
    5. 根据操作符类型生成表达式
    6. 使用 iand 将所有条件组合（AND 逻辑）

    :param model: 数据库模型类
    :param query: 查询条件字典
    :return: Peewee 表达式对象

    示例：
        query = {"status": "active", "age": (">=", 18)}
        -> 转换为 (f_status == 'active') AND (f_age >= 18)
    """
    # 存储所有转换后的表达式
    expression = []

    # 遍历查询条件字典中的每个字段
    for field, value in query.items():
        # 如果值不是列表或元组，默认使用等于操作符
        # 这样可以简化查询语法：{"name": "test"} 等价于 {"name": ("==", "test")}
        if not isinstance(value, (list, tuple)):
            value = ('==', value)

        # 解构操作符和操作数
        # value 的格式：(操作符, 操作数1, 操作数2, ...)
        # 例如：(">=", 18) 或 ("between", 10, 20)
        op, *val = value

        # 为字段名添加 f_ 前缀，获取模型字段对象
        field = getattr(model, f'f_{field}')

        # 根据操作符生成表达式
        if op in supported_operators:
            # 使用 operator 模块的函数生成二元操作表达式
            # 例如：f_age >= 18
            value = supported_operators[op](field, val[0])
        else:
            # 使用 Peewee 字段方法生成表达式
            # 例如：f_status.between('start', 'end') 或 f_name.contains('keyword')
            value = getattr(field, op)(*val)

        # 将生成的表达式添加到列表中
        expression.append(value)

    # 使用 reduce 和 iand 将所有表达式用 AND 连接
    # iand 是按位与运算符，用于 Peewee 表达式的逻辑与
    return reduce(operator.iand, expression)


def query_db(model: type[DataBaseModel], limit: int = 0, offset: int = 0,
             query: dict = None, order_by: str | list | tuple | None = None):
    """
    查询数据库

    提供通用的数据库查询功能，支持条件过滤、排序、分页。

    逻辑说明：
    1. 构建基础查询
    2. 如果有查询条件，应用 WHERE 过滤
    3. 计算总数（在分页前）
    4. 处理排序参数
    5. 为字段名添加 f_ 前缀
    6. 应用排序
    7. 应用分页限制
    8. 返回结果列表和总数

    :param model: 数据库模型类
    :param limit: 限制返回数量
    :param offset: 偏移量
    :param query: 查询条件字典
    :param order_by: 排序字段（可以是字符串或元组）
    :return: (结果列表, 总数)

    示例：
        # 查询前 10 条活跃用户，按创建时间降序
        results, total = query_db(
            User,
            limit=10,
            query={"status": "active"},
            order_by=("create_time", "desc")
        )
    """
    # 构建基础查询：选择模型的所有记录
    data = model.select()

    # 如果有查询条件，应用 WHERE 过滤
    if query:
        # 将查询字典转换为 Peewee 表达式并应用到查询中
        data = data.where(query_dict2expression(model, query))

    # 计算符合条件的记录总数
    # 注意：count() 会在应用 limit 和 offset 之前执行
    count = data.count()

    # 处理排序参数
    if not order_by:
        # 如果没有指定排序字段，默认使用 create_time
        order_by = 'create_time'

    # 如果 order_by 不是列表或元组，将其转换为元组
    # 支持两种格式：
    #   - "create_time" -> ("create_time", "asc")
    #   - ("create_time", "desc") -> 保持不变
    if not isinstance(order_by, (list, tuple)):
        order_by = (order_by, 'asc')

    # 解构排序字段和排序方向
    # order_by: 字段名，order: 排序方向（asc/desc）
    order_by, order = order_by

    # 为字段名添加 f_ 前缀，获取模型字段对象
    order_by = getattr(model, f'f_{order_by}')

    # 根据排序方向调用对应的排序方法
    # asc() 升序，desc() 降序
    order_by = getattr(order_by, order)()

    # 应用排序到查询
    data = data.order_by(order_by)

    # 应用分页限制
    if limit > 0:
        # 限制返回的记录数
        data = data.limit(limit)

    # 应用偏移量（跳过前面的记录）
    if offset > 0:
        data = data.offset(offset)

    # 执行查询并转换为列表，同时返回总数
    return list(data), count
