# RivalTrackAgent 运行前检查清单

更新日期：2026-05-29

## 环境

- 使用 conda 环境：`rivltrack`
- Python 版本：3.12.x
- 启动命令：

```powershell
conda activate rivltrack
python src/main.py
```

默认启动只打开空白仪表盘，不会自动触发 pipeline。

## 启动前

- 确认 `.env` 存在，且包含 `DEEPSEEK_API_KEY`。
- 确认 `data/demo-fallback.json` 存在（AI代码助手赛道）。
- 确认 `data/demo-fallback-milktea.json` 存在（新茶饮赛道）。
- 确认 `src/frontend/vendor/cytoscape-3.30.4.min.js` 存在。
- 确认 `src/frontend/vendor/chart-4.4.0.umd.min.js` 存在。
- 确认端口 `8080` 和 `8765` 未被占用。

## 端口占用排查

```powershell
netstat -ano | findstr :8765
netstat -ano | findstr :8080
Get-Process -Id <PID>
```

换端口启动：

```powershell
$env:WS_PORT="8766"
$env:HTTP_PORT="8081"
python src/main.py
```

## fallback 验证

```powershell
$env:DEEPSEEK_API_KEY=""
$env:PYTHONIOENCODING="utf-8"
python -c "import asyncio, sys; from src.pipeline.dag import run_pipeline; outs = asyncio.run(run_pipeline()); assert len(outs) == 5; print([o.node_id for o in outs])"
```

## 真实 pipeline 重跑

AI代码助手赛道（我方 GitHub Copilot）：

```powershell
python src/tools/run_real_pipeline.py
```

新茶饮赛道（我方 霸王茶姬）：

```powershell
python src/tools/run_real_pipeline_milktea.py
```

## 验证页面

- `http://localhost:8080/`
- `http://localhost:8080/vendor/cytoscape-3.30.4.min.js`
- `http://localhost:8080/vendor/chart-4.4.0.umd.min.js`
- `http://localhost:8080/data/demo-fallback.json`
- `http://localhost:8080/data/demo-fallback-milktea.json`

## 通过标准

- 首页 HTTP 200。
- 本地 vendor 脚本 HTTP 200。
- 两个 fallback JSON 均 HTTP 200。
- 真实 DeepSeek pipeline 返回 5 个 completed Agent 输出。
- `pytest -q` 92 个测试通过（7 个测试文件）。
- 主要 Python 模块 `py_compile` 通过。
- 前端 JS 语法检查通过（括号平衡、反引号平衡）。
- 点击"加载参考样例"弹出赛道选择器，两个赛道均可正常加载。
