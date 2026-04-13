ComponentBase 使用 ABC 确保所有组件都实现了 _invoke() 方法，保证组件系统的统一性和可靠性。
 使用 @timeout 装饰器限制执行时间，默认 10 分钟。