# Dify 抖店流程接入

本阶段将现有 Python 抖店流程接入 Dify，复用已经验证的自动关闭弹窗能力。当前只启用抖店分支，最终动作仍是保存草稿。搜款网和17网仍可作为原有商品素材来源，其目标平台工作流本轮未启用。

## 文件选择

| 文件 | 用途 |
|---|---|
| `douyin-workflow.yml` | 优先导入。无需视觉模型，通过可靠 Locator、原有步骤和弹窗处理运行 |
| `douyin-vision-workflow.yml` | 可选增强模板，导入后先为唯一的视觉 LLM 节点选择支持图片的模型 |
| `browser-tools.openapi.yml` | 可选，作为 Dify 自定义工具导入；主工作流直接用 HTTP 节点，不强制先安装工具 |
| `smoke_test.py` | 通过真实 HTTP 请求验证本地后端的14步演示，不访问账号或商品 |

工作流 DSL 按 Dify 官方仓库的 Loop、HTTP、LLM 和条件分支样例生成。已验证 YAML、节点引用、鉴权字段和本地 HTTP 协议；在用户实际云端工作区的导入、连通性和视觉模型调用仍需联调，不能将文件生成等同于云端验收。

## 1. 启动本机后端

在项目根目录安装依赖：

```powershell
E:\Try-onApp\.venv\Scripts\python.exe -m pip install -r requirements-dify.txt
```

双击根目录 `run-dify-bridge.bat`。首次运行自动生成 `.env.dify.local`，随后监听 `http://127.0.0.1:8765`。

默认只允许本地演示，不读取店铺登录环境、不创建真实商品。直接使用 Python 启动的等价方式：

```powershell
python run_dify_bridge.py --init
python run_dify_bridge.py
```

在另一个终端运行端到端演示：

```powershell
python dify/smoke_test.py
```

程序打开独立 Edge 窗口，逐步关闭弹窗并继续执行；完成后输出 `DIFY_BRIDGE_SMOKE_OK`，报告明确标记 `simulation=true`。这验证本地后端和浏览器执行链路，不代表已经在云端 Dify 运行。

## 2. 让云端 Dify 访问本机服务

云端 Dify 的 `localhost` 指向云端自身，不能填成本机地址。需要为本机端口8765配置一个可由云端访问的 HTTPS 反向代理或隧道地址。系统不会自动创建公网隧道或更改防火墙。

准备好地址后，在 Dify 导入 `douyin-workflow.yml`，设置两个工作流环境变量：

| 变量 | 填写内容 |
|---|---|
| `BROWSER_BASE_URL` | 实际 HTTPS 后端地址，不以 `/` 结尾，替换示例域名 |
| `BROWSER_API_KEY` | 本机 `.env.dify.local` 中的工具密钥，保持 Secret 类型 |

这是浏览器工具的本机密钥，不是 Dify 平台的 API Key，不需要发送到聊天。工具请求均带 `X-Browser-Key`；截图也需要同样鉴权。

若选择导入自定义工具，需要同时把 `browser-tools.openapi.yml` 中 `servers.url` 的示例地址替换为实际地址，鉴权选择请求头 `X-Browser-Key`。

## 3. 先运行云端演示

工作流输入 `mode=demo`，其他参数留空。Dify 的处理顺序为：

```text
参数校验 → 创建本机任务 → 循环读取状态
    IDLE          → 调用明确的下一个步骤
    RUNNING       → 有限等待再读取状态
    WAITING_HUMAN → 返回当前问题和任务编号
    SUCCESS/FAILED/中断 → 返回实际结果
```

同一任务一次只执行一个步骤，重试已完成的步骤返回已有结果。后端一次只允许一个任务占用专用浏览器。Dify 因时间上限退出后，后端状态仍可查询；使用返回或节点日志中的任务编号填写 `resume_task_id` 继续，不要重复新建商品任务。

## 4. 启用真实抖店草稿

关闭正在使用同一专用 Edge 登录目录的旧自动化任务，停止演示后端后以真实模式启动：

```powershell
python run_dify_bridge.py --allow-live
```

工作流填写：

```text
mode: live
source_url: 供应商商品链接
save_directory: D:\商品素材\抖店\2026-10-21
resume_task_id: 新任务留空
```

真实模式复用原 `config.json` 的 Edge、扩展、登录目录、洗水标及商品配置。下载图片保存在指定目录下的独立任务子目录中，结果返回实际路径。现有业务预设仍会生效，包括现货库存0、预售库存10000、尺码及发货设置；本轮没有更改这些原有参数。

Dify 调用以下14个步骤，后端复用原函数执行：

1. 读取与下载商品素材。
2. 打开抖店商品创建。
3. 上传主图。
4. 确认官方 AI 生成。
5. 等待官方 AI 生成完成。
6. 上传主图视频；没有视频则记录跳过。
7. 上传水洗标／吊牌图。
8. 选择适用年龄；相关类目按原流程跳过。
9. 填写发货及价格库存选项。
10. 批量设置库存。
11. 选择尺码大小。
12. 填写尺码备注。
13. 填写尺码表。
14. 保存草稿，并等待实际成功提示及保存证据截图。

登录、验证码及不确定窗口暂停等待本机人工。人工处理后，在工作流填写原任务编号重新运行，已完成的步骤不会重做。后端重启会把未完成任务标记为 `INTERRUPTED`，必须先核查，不会自动重发旧操作。

## 5. 配置视觉增强版

当前用户尚未配置视觉模型，因此优先使用无需模型版。增强模板保留了空模型配置，选择实际模型后才可使用；没有默认假定任何模型或供应商已经可用。

导入 `douyin-vision-workflow.yml`，为“仅定位关键词目标”节点选择已安装且支持图片输入的模型。也可按工作区实际提供者标识重新生成文件：

```powershell
python dify/build_workflows.py --vision-provider 实际提供者标识 --vision-model 实际模型名称
```

增强版在可靠 Locator 不存在时使用这一条分支：

```text
本机截取当前目标区域 → HTTP 返回真正的 PNG 文件
→ LLM 的图片变量接收该文件，仅识别当前关键词
→ 严格解析 found/target/x/y/confidence
→ 后端检查目标、置信度、区域边界及画面是否仍一致
→ 执行原步骤，并验证该步骤的预期结果
```

`roi_json` 可以指定当前视口中的较大搜索区域，例如：

```json
{"x":0.2,"y":0.15,"width":0.6,"height":0.65,"normalized":true}
```

不传区域时使用当前视口截图，模型仍只找指定目标，不枚举其他组件。本阶段尚未实现从用户圈选参考图片自动配准区域；可先填区域参数。坐标始终相对于实际送入模型的图片，由本机加裁剪偏移换算，截图使用 CSS 像素以处理高 DPI。

画面改变、滚动、视口改变、结果过期、多目标歧义、错误目标、低置信度或无法检查的跨框架坐标均不会盲点。相同目标最多请求两次视觉识别，等待识别超过180秒转人工处理。截图90秒后不能用于点击。

## API 说明

| 接口 | 行为 |
|---|---|
| `GET /health` | 不含账号信息的服务健康检查 |
| `POST /tasks` | 创建演示或真实草稿任务；请求编号用于防止重复创建 |
| `GET /tasks/{id}?wait_seconds=15` | 查询当前步骤；短暂等待正在执行的步骤 |
| `POST /tasks/{id}/advance` | 按后端给出的 `next_stage` 执行下一步骤 |
| `POST /tasks/{id}/resume` | 人工处理后继续，或在云端超时后继续查询 |
| `POST /tasks/{id}/cancel` | 停止任务并释放专用浏览器 |
| `GET /tasks/{id}/screenshots/{screenshot_id}.png` | 仅返回当前任务登记的截图 |
| `POST /tasks/{id}/vision` | 接收识别结果，校验后继续原操作 |

除健康检查外全部需要本机工具密钥。无需给模型系统命令、任意脚本、本地任意文件读取或正式发布能力。

## 测试

```powershell
python -m unittest discover -s test_python -p test_dify_bridge.py -v
python -m unittest discover -s test_python -v
python dify/smoke_test.py
```

测试中的浏览器页面和模型输出替身仅验证协议、坐标与执行边界；真实抖店任务、真实云端导入及视觉模型识别单独验收。

官方字段依据：[Dify 工具](https://docs.dify.ai/en/cloud/use-dify/workspace/tools)、[HTTP 节点](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)、[LLM 图片输入](https://docs.dify.ai/en/cloud/use-dify/nodes/llm)、[官方 Loop 样例](https://github.com/langgenius/dify/blob/main/api/tests/fixtures/workflow/increment_loop_with_break_condition_workflow.yml)。
