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

import asyncio
import re
import time
from abc import ABC
import builtins
import json
import os
import logging
from typing import Any, List, Union
import pandas as pd
from agent import settings
from common.connection_utils import timeout



from common.misc_utils import thread_pool_exec

_FEEDED_DEPRECATED_PARAMS = "_feeded_deprecated_params"
_DEPRECATED_PARAMS = "_deprecated_params"
_USER_FEEDED_PARAMS = "_user_feeded_params"
_IS_RAW_CONF = "_is_raw_conf"


class ComponentParamBase(ABC):
    """
    组件参数基类，定义了组件参数的基本结构和验证方法
    """
    def __init__(self):
        # 消息历史窗口大小，默认13
        self.message_history_window_size = 13
        # 输入参数字典
        self.inputs = {}
        # 输出参数字典
        self.outputs = {}
        # 组件描述
        self.description = ""
        # 最大重试次数
        self.max_retries = 0
        # 错误后延迟时间
        self.delay_after_error = 2.0
        # 异常处理方式
        self.exception_method = None
        # 异常时的默认值
        self.exception_default_value = None
        # 异常时跳转到的组件
        self.exception_goto = None
        # 调试输入
        self.debug_inputs = {}

    def set_name(self, name: str):
        """设置组件名称"""
        self._name = name
        return self

    def check(self):
        """参数检查的抽象方法，子类必须实现"""
        raise NotImplementedError("Parameter Object should be checked.")

    @classmethod
    def _get_or_init_deprecated_params_set(cls):
        """获取或初始化废弃参数集合"""
        if not hasattr(cls, _DEPRECATED_PARAMS):
            setattr(cls, _DEPRECATED_PARAMS, set())
        return getattr(cls, _DEPRECATED_PARAMS)

    def _get_or_init_feeded_deprecated_params_set(self, conf=None):
        """获取或初始化已提供废弃参数集合"""
        if not hasattr(self, _FEEDED_DEPRECATED_PARAMS):
            if conf is None:
                setattr(self, _FEEDED_DEPRECATED_PARAMS, set())
            else:
                setattr(
                    self,
                    _FEEDED_DEPRECATED_PARAMS,
                    set(conf[_FEEDED_DEPRECATED_PARAMS]),
                )
        return getattr(self, _FEEDED_DEPRECATED_PARAMS)

    def _get_or_init_user_feeded_params_set(self, conf=None):
        """获取或初始化用户提供的参数集合"""
        if not hasattr(self, _USER_FEEDED_PARAMS):
            if conf is None:
                setattr(self, _USER_FEEDED_PARAMS, set())
            else:
                setattr(self, _USER_FEEDED_PARAMS, set(conf[_USER_FEEDED_PARAMS]))
        return getattr(self, _USER_FEEDED_PARAMS)

    def get_user_feeded(self):
        """获取用户提供的参数集合"""
        return self._get_or_init_user_feeded_params_set()

    def get_feeded_deprecated_params(self):
        """获取已提供的废弃参数集合"""
        return self._get_or_init_feeded_deprecated_params_set()

    @property
    def _deprecated_params_set(self):
        """获取废弃参数集合"""
        return {name: True for name in self.get_feeded_deprecated_params()}

    def __str__(self):
        """将参数对象转换为JSON字符串"""
        return json.dumps(self.as_dict(), ensure_ascii=False)

    def as_dict(self):
        """将参数对象转换为字典格式"""
        def _recursive_convert_obj_to_dict(obj):
            ret_dict = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, dict) or (v and type(v).__name__ not in dir(builtins)):
                        ret_dict[k] = _recursive_convert_obj_to_dict(v)
                    else:
                        ret_dict[k] = v
                return ret_dict

            for attr_name in list(obj.__dict__):
                if attr_name in [_FEEDED_DEPRECATED_PARAMS, _DEPRECATED_PARAMS, _USER_FEEDED_PARAMS, _IS_RAW_CONF]:
                    continue
                # 获取属性
                attr = getattr(obj, attr_name)
                if isinstance(attr, pd.DataFrame):
                    ret_dict[attr_name] = attr.to_dict()
                    continue
                if isinstance(attr, dict) or (attr and type(attr).__name__ not in dir(builtins)):
                    ret_dict[attr_name] = _recursive_convert_obj_to_dict(attr)
                else:
                    ret_dict[attr_name] = attr

            return ret_dict

        return _recursive_convert_obj_to_dict(self)

    def update(self, conf, allow_redundant=False):
        """更新参数配置"""
        update_from_raw_conf = conf.get(_IS_RAW_CONF, True)
        if update_from_raw_conf:
            deprecated_params_set = self._get_or_init_deprecated_params_set()
            feeded_deprecated_params_set = (
                self._get_or_init_feeded_deprecated_params_set()
            )
            user_feeded_params_set = self._get_or_init_user_feeded_params_set()
            setattr(self, _IS_RAW_CONF, False)
        else:
            feeded_deprecated_params_set = (
                self._get_or_init_feeded_deprecated_params_set(conf)
            )
            user_feeded_params_set = self._get_or_init_user_feeded_params_set(conf)

        def _recursive_update_param(param, config, depth, prefix):
            if depth > settings.PARAM_MAXDEPTH:
                raise ValueError("Param define nesting too deep!!!, can not parse it")

            inst_variables = param.__dict__
            redundant_attrs = []
            for config_key, config_value in config.items():
                # 冗余属性
                if config_key not in inst_variables:
                    if not update_from_raw_conf and config_key.startswith("_"):
                        setattr(param, config_key, config_value)
                    else:
                        setattr(param, config_key, config_value)
                        # redundant_attrs.append(config_key)
                    continue

                full_config_key = f"{prefix}{config_key}"

                if update_from_raw_conf:
                    # 添加用户提供的参数
                    user_feeded_params_set.add(full_config_key)

                    # 更新用户提供废弃参数集
                    if full_config_key in deprecated_params_set:
                        feeded_deprecated_params_set.add(full_config_key)

                # 支持的属性
                attr = getattr(param, config_key)
                if type(attr).__name__ in dir(builtins) or attr is None:
                    setattr(param, config_key, config_value)

                else:
                    # 递归设置对象属性
                    sub_params = _recursive_update_param(
                        attr, config_value, depth + 1, prefix=f"{prefix}{config_key}."
                    )
                    setattr(param, config_key, sub_params)

            if not allow_redundant and redundant_attrs:
                raise ValueError(
                    f"cpn `{getattr(self, '_name', type(self))}` has redundant parameters: `{[redundant_attrs]}`"
                )

            return param

        return _recursive_update_param(param=self, config=conf, depth=0, prefix="")

    def extract_not_builtin(self):
        """提取非内置类型的参数"""
        def _get_not_builtin_types(obj):
            ret_dict = {}
            for variable in obj.__dict__:
                attr = getattr(obj, variable)
                if attr and type(attr).__name__ not in dir(builtins):
                    ret_dict[variable] = _get_not_builtin_types(attr)

            return ret_dict

        return _get_not_builtin_types(self)

    def validate(self):
        """验证参数的有效性"""
        self.builtin_types = dir(builtins)
        self.func = {
            "ge": self._greater_equal_than,
            "le": self._less_equal_than,
            "in": self._in,
            "not_in": self._not_in,
            "range": self._range,
        }
        home_dir = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))
        param_validation_path_prefix = home_dir + "/param_validation/"

        param_name = type(self).__name__
        param_validation_path = "/".join(
            [param_validation_path_prefix, param_name + ".json"]
        )

        validation_json = None

        try:
            with open(param_validation_path, "r") as fin:
                validation_json = json.loads(fin.read())
        except BaseException:
            return

        self._validate_param(self, validation_json)

    def _validate_param(self, param_obj, validation_json):
        """验证参数"""
        default_section = type(param_obj).__name__
        var_list = param_obj.__dict__

        for variable in var_list:
            attr = getattr(param_obj, variable)

            if type(attr).__name__ in self.builtin_types or attr is None:
                if variable not in validation_json:
                    continue

                validation_dict = validation_json[default_section][variable]
                value = getattr(param_obj, variable)
                value_legal = False

                for op_type in validation_dict:
                    if self.func[op_type](value, validation_dict[op_type]):
                        value_legal = True
                        break

                if not value_legal:
                    raise ValueError(
                        "Please check runtime conf, {} = {} does not match user-parameter restriction".format(
                            variable, value
                        )
                    )

            elif variable in validation_json:
                self._validate_param(attr, validation_json)

    @staticmethod
    def check_string(param, description):
        """检查参数是否为字符串类型"""
        if type(param).__name__ not in ["str"]:
            raise ValueError(description + " {} not supported, should be string type".format(param))

    @staticmethod
    def check_empty(param, description):
        """检查参数是否为空"""
        if not param:
            raise ValueError(description + " does not support empty value.")

    @staticmethod
    def check_positive_integer(param, description):
        """检查参数是否为正整数"""
        if type(param).__name__ not in ["int", "long"] or param <= 0:
            raise ValueError(description + " {} not supported, should be positive integer".format(param))

    @staticmethod
    def check_positive_number(param, description):
        """检查参数是否为正数"""
        if type(param).__name__ not in ["float", "int", "long"] or param <= 0:
            raise ValueError(description + " {} not supported, should be positive numeric".format(param))

    @staticmethod
    def check_nonnegative_number(param, description):
        """检查参数是否为非负数"""
        if type(param).__name__ not in ["float", "int", "long"] or param < 0:
            raise ValueError(description + " {} not supported, should be non-negative numeric".format(param))

    @staticmethod
    def check_decimal_float(param, description):
        """检查参数是否为0-1之间的浮点数"""
        if type(param).__name__ not in ["float", "int"] or param < 0 or param > 1:
            raise ValueError(description + " {} not supported, should be a float number in range [0, 1]".format(param))

    @staticmethod
    def check_boolean(param, description):
        """检查参数是否为布尔类型"""
        if type(param).__name__ != "bool":
            raise ValueError(description + " {} not supported, should be bool type".format(param))

    @staticmethod
    def check_open_unit_interval(param, description):
        """检查参数是否在开区间(0,1)内"""
        if type(param).__name__ not in ["float"] or param <= 0 or param >= 1:
            raise ValueError(description + " should be a numeric number between 0 and 1 exclusively")

    @staticmethod
    def check_valid_value(param, description, valid_values):
        """检查参数值是否在有效值列表中"""
        if param not in valid_values:
            raise ValueError(description + " {} is not supported, it should be in {}".format(param, valid_values))

    @staticmethod
    def check_defined_type(param, description, types):
        """检查参数类型是否为指定类型之一"""
        if type(param).__name__ not in types:
            raise ValueError(description + " {} not supported, should be one of {}".format(param, types))

    @staticmethod
    def check_and_change_lower(param, valid_list, description=""):
        """检查参数是否在有效列表中，并转换为小写"""
        if type(param).__name__ != "str":
            raise ValueError(description + " {} not supported, should be one of {}".format(param, valid_list))

        lower_param = param.lower()
        if lower_param in valid_list:
            return lower_param
        else:
            raise ValueError(description + " {} not supported, should be one of {}".format(param, valid_list))

    @staticmethod
    def _greater_equal_than(value, limit):
        """检查值是否大于等于限制值"""
        return value >= limit - settings.FLOAT_ZERO

    @staticmethod
    def _less_equal_than(value, limit):
        """检查值是否小于等于限制值"""
        return value <= limit + settings.FLOAT_ZERO

    @staticmethod
    def _range(value, ranges):
        """检查值是否在指定范围内"""
        in_range = False
        for left_limit, right_limit in ranges:
            if (
                    left_limit - settings.FLOAT_ZERO
                    <= value
                    <= right_limit + settings.FLOAT_ZERO
            ):
                in_range = True
                break

        return in_range

    @staticmethod
    def _in(value, right_value_list):
        """检查值是否在列表中"""
        return value in right_value_list

    @staticmethod
    def _not_in(value, wrong_value_list):
        """检查值是否不在列表中"""
        return value not in wrong_value_list

    def _warn_deprecated_param(self, param_name, description):
        """警告已废弃的参数"""
        if self._deprecated_params_set.get(param_name):
            logging.warning(
                f"{description} {param_name} is deprecated and ignored in this version."
            )

    def _warn_to_deprecate_param(self, param_name, description, new_param):
        """警告即将废弃的参数"""
        if self._deprecated_params_set.get(param_name):
            logging.warning(
                f"{description} {param_name} will be deprecated in future release; "
                f"please use {new_param} instead."
            )
            return True
        return False

class ComponentBase(ABC):
    """
    Agent 组件基类

    所有 Agent 工作流组件的基础类，定义了组件的核心行为：
    - 组件初始化和参数验证
    - 组件执行（invoke）
    - 输入输出管理
    - 变量引用解析
    - 任务取消检查

    Attributes:
        component_name (str): 组件名称，子类必须定义此属性
        thread_limiter (asyncio.Semaphore): 并发控制信号量，限制同时运行的聊天数量
        variable_ref_patt (str): 变量引用的正则表达式模式
                                  匹配格式：{component_id@output}、{sys.variable}、{env.variable}

    Example:
        >>> class MyComponent(ComponentBase):
        >>>     component_name = "MyComponent"
        >>>
        >>>     def _invoke(self, **kwargs):
        >>>         # 组件执行逻辑
        >>>         result = "Hello"
        >>>         self.set_output("content", result)
    """

    component_name: str  # 组件名称，子类必须定义
    # 并发限制：控制同时运行的最大聊天数，默认 10
    thread_limiter = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENT_CHATS", 10)))
    # 变量引用正则：匹配 {component_id@output}、{sys.variable}、{env.variable}
    variable_ref_patt = r"\{* *\{([a-zA-Z:0-9]+@[A-Za-z0-9_.-]+|sys\.[A-Za-z0-9_.]+|env\.[A-Za-z0-9_.]+)\} *\}*"

    def __str__(self):
        """
        返回组件的 JSON 字符串表示

        用于序列化组件配置，包含组件名称和参数。

        Returns:
            str: JSON 格式的组件配置字符串

        Example:
            >>> str(component)
            '{"component_name": "Begin", "params": {}}'
        """
        return """{{
            "component_name": "{}",
            "params": {}
        }}""".format(self.component_name,
                     self._param
                     )

    def __init__(self, canvas, id, param: ComponentParamBase):
        """
        初始化组件实例

        Args:
            canvas: Canvas 实例，提供组件间的通信和状态管理
            id: 组件的唯一标识符
            param: 组件参数对象，包含组件的配置参数

        Raises:
            AssertionError: 如果 canvas 不是 Graph/Canvas 实例
            ValueError: 如果参数验证失败（通过 param.check()）
        """
        # 本地导入避免循环依赖
        from agent.canvas import Graph  # Local import to avoid cyclic dependency

        # 验证 canvas 类型
        assert isinstance(canvas, Graph), "canvas must be an instance of Canvas"
        self._canvas = canvas      # 保存 Canvas 引用，用于访问全局状态和其他组件
        self._id = id              # 组件的唯一 ID
        self._param = param        # 组件参数对象
        self._param.check()        # 验证参数有效性，不合法会抛出异常

    def is_canceled(self) -> bool:
        """
        检查任务是否已被取消

        通过调用 Canvas 的 is_canceled 方法来检查任务状态。
        最终检查 Redis 中的取消标志。

        Returns:
            bool: 如果任务已取消返回 True，否则返回 False
        """
        return self._canvas.is_canceled()

    def check_if_canceled(self, message: str = "") -> bool:
        """
        检查任务是否已取消，如果已取消则记录日志并设置错误输出

        这是组件执行过程中的取消检查点，应该在长时间操作前调用。
        如果任务已取消，会：
        1. 记录取消日志（包含任务 ID 和当前操作描述）
        2. 设置组件的 _ERROR 输出
        3. 返回 True 让调用者知道应该停止执行

        Args:
            message: 当前操作的描述信息，用于日志记录
                    例如：'Begin processing', 'LLM invocation' 等

        Returns:
            bool: 如果任务已取消返回 True，否则返回 False

        Example:
            >>> # 在组件执行开始时检查
            >>> if self.check_if_canceled("Start processing"):
            >>>     return  # 停止执行
            >>>
            >>> # 在循环中定期检查
            >>> for item in items:
            >>>     if self.check_if_canceled("Processing items"):
            >>>         return  # 及时响应用户取消
            >>>     process(item)
        """
        if self.is_canceled():
            # 获取任务 ID，如果无法获取则使用 'unknown'
            task_id = getattr(self._canvas, 'task_id', 'unknown')
            # 构建取消日志消息
            log_message = f"Task {task_id} has been canceled"
            # 如果提供了操作描述，追加到日志消息中
            if message:
                log_message += f" during {message}"
            # 记录取消日志
            logging.info(log_message)
            # 设置组件的错误输出，标记任务已取消
            self.set_output("_ERROR", "Task has been canceled")
            return True
        return False

    def invoke(self, **kwargs) -> dict[str, Any]:
        """
        同步调用组件执行

        这是组件的主要执行入口，负责：
        1. 记录开始时间
        2. 调用子类实现的 _invoke 方法
        3. 处理异常和错误
        4. 记录执行时间

        Args:
            **kwargs: 传递给组件的参数

        Returns:
            dict[str, Any]: 组件的输出字典
        """
        # 记录组件开始执行的时间
        self.set_output("_created_time", time.perf_counter())
        try:
            # 调用子类实现的 _invoke 方法执行组件逻辑
            self._invoke(**kwargs)
        except Exception as e:
            # 如果配置了异常默认值，使用默认值
            if self.get_exception_default_value():
                self.set_exception_default_value()
            else:
                # 否则设置错误输出
                self.set_output("_ERROR", str(e))
            # 记录异常日志
            logging.exception(e)
        # 清空调试输入
        self._param.debug_inputs = {}
        # 计算并设置执行耗时
        self.set_output("_elapsed_time", time.perf_counter() - self.output("_created_time"))
        # 返回所有输出
        return self.output()

    async def invoke_async(self, **kwargs) -> dict[str, Any]:
        """
        异步调用组件执行

        优先使用子类的 _invoke_async 方法（如果存在且是协程函数），
        否则回退到同步的 _invoke 方法（在线程池中执行）。

        Args:
            **kwargs: 传递给组件的参数

        Returns:
            dict[str, Any]: 组件的输出字典
        """
        # 记录组件开始执行的时间
        self.set_output("_created_time", time.perf_counter())
        try:
            # 检查任务是否已取消
            if self.check_if_canceled("Component processing"):
                return

            # 尝试获取异步执行方法
            fn_async = getattr(self, "_invoke_async", None)
            if fn_async and asyncio.iscoroutinefunction(fn_async):
                # 如果 _invoke_async 是协程函数，直接调用
                await fn_async(**kwargs)
            elif asyncio.iscoroutinefunction(self._invoke):
                # 如果 _invoke 是协程函数，直接调用
                await self._invoke(**kwargs)
            else:
                # 否则在线程池中执行同步方法
                await thread_pool_exec(self._invoke, **kwargs)
        except Exception as e:
            # 异常处理逻辑与同步方法相同
            if self.get_exception_default_value():
                self.set_exception_default_value()
            else:
                self.set_output("_ERROR", str(e))
            logging.exception(e)
        # 清调试输入和记录耗时
        self._param.debug_inputs = {}
        self.set_output("_elapsed_time", time.perf_counter() - self.output("_created_time"))
        return self.output()

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        """
        组件执行的核心逻辑（抽象方法）

        子类必须实现此方法，定义组件的具体执行逻辑。
        使用 @timeout 装饰器限制执行时间，默认 10 分钟。

        Args:
            **kwargs: 组件输入参数

        Raises:
            NotImplementedError: 子类未实现此方法时抛出
            TimeoutError: 执行超时时抛出
        """
        raise NotImplementedError()

    def output(self, var_nm: str = None) -> Union[dict[str, Any], Any]:
        """
        获取组件输出

        Args:
            var_nm: 变量名，如果为 None 则返回所有输出

        Returns:
            Union[dict[str, Any], Any]:
                - 如果指定 var_nm：返回该变量的值
                - 如果 var_nm 为 None：返回所有输出的字典
        """
        if var_nm:
            # 返回指定变量的值
            return self._param.outputs.get(var_nm, {}).get("value", "")
        # 返回所有输出的字典
        return {k: o.get("value") for k, o in self._param.outputs.items()}

    def set_output(self, key: str, value: Any):
        """
        设置组件输出

        Args:
            key: 输出变量名
            value: 输出值
        """
        # 如果输出变量不存在，先初始化
        if key not in self._param.outputs:
            self._param.outputs[key] = {"value": None, "type": str(type(value))}
        # 设置输出值
        self._param.outputs[key]["value"] = value

    def error(self):
        """
        获取组件的错误信息

        Returns:
            str: 错误信息，如果没有错误则返回 None
        """
        return self._param.outputs.get("_ERROR", {}).get("value")

    def reset(self, only_output=False):
        """
        重置组件状态

        Args:
            only_output: 如果为 True，只重置输出；否则同时重置输入和调试输入
        """
        # 重置所有输出值为 None
        outputs: dict = self._param.outputs  # for better performance
        for k in outputs.keys():
            outputs[k]["value"] = None
        # 如果只需要重置输出，直接返回
        if only_output:
            return

        # 重置所有输入值和调试输入
        inputs: dict = self._param.inputs  # for better performance
        for k in inputs.keys():
            inputs[k]["value"] = None
        self._param.debug_inputs = {}

    def get_input(self, key: str = None) -> Union[Any, dict[str, Any]]:
        """
        获取组件输入

        如果输入值是变量引用，会自动解析为实际值。
        如果 key 为 None，返回所有输入的字典。

        Args:
            key: 输入变量名

        Returns:
            Union[Any, dict[str, Any]]: 输入值或所有输入的字典
        """
        if key:
            # 返回指定输入的值
            return self._param.inputs.get(key, {}).get("value")

        # 获取所有输入，解析变量引用
        res = {}
        for var, o in self.get_input_elements().items():
            # 获取参数值
            v = self.get_param(var)
            if v is None:
                continue
            # 如果是变量引用，解析为实际值
            if isinstance(v, str) and self._canvas.is_reff(v):
                self.set_input_value(var, self._canvas.get_variable_value(v))
            else:
                self.set_input_value(var, v)
            res[var] = self.get_input_value(var)
        return res

    def get_input_values(self) -> Union[Any, dict[str, Any]]:
        """
        获取组件的所有输入值

        如果存在调试输入（debug_inputs），则返回调试输入；
        否则返回实际的输入值。

        Returns:
            dict[str, Any]: 所有输入值的字典
        """
        # 优先返回调试输入
        if self._param.debug_inputs:
            return self._param.debug_inputs

        # 返回实际输入值
        return {var: self.get_input_value(var) for var, o in self.get_input_elements().items()}

    def get_input_elements_from_text(self, txt: str) -> dict[str, dict[str, str]]:
        """
        从文本中提取变量引用并解析

        扫描文本中的变量引用（如 {component_id@output}、{sys.variable} 等），
        返回每个引用的详细信息。

        Args:
            txt: 要扫描的文本

        Returns:
            dict[str, dict[str, str]]: 变量引用的字典，每个引用包含：
                - name: 显示名称
                - value: 解析后的值
                - _retrieval: 检索结果（如果有）
                - _cpn_id: 组件 ID
        """
        res = {}
        # 使用正则表达式查找所有变量引用
        for r in re.finditer(self.variable_ref_patt, txt, flags=re.IGNORECASE | re.DOTALL):
            exp = r.group(1)
            # 解析组件 ID 和变量名
            cpn_id, var_nm = exp.split("@") if exp.find("@") > 0 else ("", exp)
            res[exp] = {
                # 构建显示名称
                "name": (self._canvas.get_component_name(cpn_id) + f"@{var_nm}") if cpn_id else exp,
                # 解析变量值
                "value": self._canvas.get_variable_value(exp),
                # 获取检索结果
                "_retrieval": self._canvas.get_variable_value(f"{cpn_id}@_references") if cpn_id else None,
                # 组件 ID
                "_cpn_id": cpn_id
            }
        return res

    def get_input_elements(self) -> dict[str, Any]:
        """
        获取所有输入元素

        Returns:
            dict[str, Any]: 所有输入的字典
        """
        return self._param.inputs

    def get_input_form(self) -> dict[str, dict]:
        """
        获取输入表单定义

        Returns:
            dict[str, dict]: 输入表单的配置字典
        """
        return self._param.get_input_form()

    def set_input_value(self, key: str, value: Any) -> None:
        """
        设置输入值

        Args:
            key: 输入变量名
            value: 输入值
        """
        # 如果输入不存在，先初始化
        if key not in self._param.inputs:
            self._param.inputs[key] = {"value": None}
        # 设置输入值
        self._param.inputs[key]["value"] = value

    def get_input_value(self, key: str) -> Any:
        """
        获取指定输入的值

        Args:
            key: 输入变量名

        Returns:
            Any: 输入值，如果不存在则返回 None
        """
        if key not in self._param.inputs:
            return None
        return self._param.inputs[key].get("value")

    def get_component_name(self, cpn_id) -> str:
        """
        获取指定组件的名称

        Args:
            cpn_id: 组件 ID

        Returns:
            str: 组件名称（小写）
        """
        return self._canvas.get_component(cpn_id)["obj"].component_name.lower()

    def get_param(self, name):
        """
        获取组件参数的值

        Args:
            name: 参数名称

        Returns:
            参数值，如果参数不存在则返回 None
        """
        if hasattr(self._param, name):
            return getattr(self._param, name)
        return None

    def debug(self, **kwargs):
        """
        调试模式执行组件

        直接调用 _invoke 方法，不经过 invoke 的包装逻辑。
        用于开发和调试。

        Args:
            **kwargs: 组件输入参数

        Returns:
            _invoke 方法的返回值
        """
        return self._invoke(**kwargs)

    def get_parent(self) -> Union[object, None]:
        """
        获取父组件

        用于循环组件（Iteration/Loop）等有层级结构的组件。

        Returns:
            Union[object, None]: 父组件对象，如果没有父组件则返回 None
        """
        # 获取父组件 ID
        pid = self._canvas.get_component(self._id).get("parent_id")
        if not pid:
            return None
        # 返回父组件对象
        return self._canvas.get_component(pid)["obj"]

    def get_upstream(self) -> List[str]:
        """
        获取上游组件 ID 列表

        Returns:
            List[str]: 上游组件的 ID 列表
        """
        cpn_nms = self._canvas.get_component(self._id)['upstream']
        return cpn_nms

    def get_downstream(self) -> List[str]:
        """
        获取下游组件 ID 列表

        Returns:
            List[str]: 下游组件的 ID 列表
        """
        cpn_nms = self._canvas.get_component(self._id)['downstream']
        return cpn_nms

    @staticmethod
    def string_format(content: str, kv: dict[str, str]) -> str:
        """
        使用键值对替换字符串中的变量占位符

        将字符串中的 {key} 替换为对应的值。

        Args:
            content: 包含变量占位符的字符串
            kv: 键值对字典

        Returns:
            str: 替换后的字符串

        Example:
            >>> string_format("Hello {name}", {"name": "World"})
            'Hello World'
        """
        for n, v in kv.items():
            def repl(_match, val=v):
                return str(val) if val is not None else ""

            # 替换所有 {key} 格式的占位符
            content = re.sub(
                r"\{%s\}" % re.escape(n),
                repl,
                content
            )
        return content

    def exception_handler(self):
        """
        获取异常处理配置

        Returns:
            dict: 包含异常处理配置的字典，如果没有配置则返回 None
                - goto: 异常时跳转到的组件 ID
                - default_value: 异常时的默认返回值
        """
        if not self._param.exception_method:
            return None
        return {
            "goto": self._param.exception_goto,
            "default_value": self._param.exception_default_value
        }

    def get_exception_default_value(self):
        """
        获取异常时的默认值

        只有当异常处理方法为 "comment" 时才返回默认值。

        Returns:
            str: 异常默认值，如果没有配置则返回空字符串
        """
        if self._param.exception_method != "comment":
            return ""
        return self._param.exception_default_value

    def set_exception_default_value(self):
        """
        设置异常默认值作为组件输出

        当组件执行出错时，将配置的默认值设置为 result 输出。
        """
        self.set_output("result", self.get_exception_default_value())

    def thoughts(self) -> str:
        """
        获取组件的思考过程

        子类可以重写此方法，返回组件执行过程中的思考或推理过程。
        用于向用户展示 AI 的"思考"过程。

        Returns:
            str: 思考过程的文本描述

        Raises:
            NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError()