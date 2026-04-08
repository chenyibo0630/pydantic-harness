  请求进入                                                                                                                                                                                                                         
    │                                                                                                                                                                                                                              
    ├─ 1. instructions / @agent.system_prompt    ← 动态注入系统提示                                                                                                                                                                
    │     (函数版可根据 deps 动态生成)                                                                                                                                                                                          
    │
    ├─ 2. history_processors                     ← 消息历史预处理
    │     发给模型之前变换 message list
    │     (裁剪、过滤、注入上下文)
    │
    ├─ 3. model_settings (callable)              ← 动态模型参数
    │     每次请求前调用，可按上下文调整 temperature 等
    │
    ├─ 4. prepare_tools                          ← 每步动态过滤工具
    │     (按权限/场景决定暴露哪些工具)
    │
    ├─ 5. tool 函数本身                           ← 工具执行
    │     (RunContext 提供 deps 注入)
    │
    ├─ 6. @agent.output_validator                ← 输出校验/变换
    │     模型返回后校验，不通过可重试
    │
    └─ 7. instrument (OpenTelemetry)             ← 可观测性/tracing