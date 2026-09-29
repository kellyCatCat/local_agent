# Skill 维护 Agent

本地运行的网页版 agent：上传补充子场景诊断步骤的 Excel / Markdown，由模型按 `templates/skill_template.md`
新建或修改本地 skill，支持多轮追问修改、与原版 diff、格式检查、下载和写回本地 skill 库。

## 启动

```bash
pip install -r requirements.txt
cp .env.example .env      # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / SKILLS_DIR
python -m app             # 打开 http://127.0.0.1:8000
```

`.env` 已在 `.gitignore` 中，模型地址和密钥不会进入代码仓。模型接口需兼容 OpenAI `POST {LLM_BASE_URL}/chat/completions`（流式）。

## 使用流程

1. **上传源文档**：`.xlsx`（每个 sheet 转成 markdown 表格，合并单元格会自动展开）、`.md`、`.csv`、`.txt`，可多个。点击文件名可查看交给模型的解析结果。
2. **选择修改方式**：点「分析并推荐」，模型对比 skill 库，建议合并进某个已有 skill 或新增 skill；可以按推荐操作，也可以手动选择要修改的 skill，或填写英文 slug 新增 skill（按模板要求，name 由调用方给出语义英文名，不要音译）。
3. **生成**：填写补充要求（可选）后点「开始生成」，模型按模板流式输出，右侧实时显示正在写入的文件。
4. **追问修改**：继续对话即可；模型只输出有改动的文件，每次改动生成一个草稿版本。后续上传的源文档会在下一轮一起发给模型。
5. **检查与对比**：右侧可以编辑源码（保存为新版本），和原版或上一版做 diff，也可以切换或恢复历史版本。格式检查会按模板校验单故障和多场景两种布局，点「让模型按检查结果修正」可以把问题交给模型修改。
6. **下载 / 写回**：「下载最新 skill」打包当前草稿（zip）。「写回 skill 库」会先把原目录整体备份到 `data/backups/<name>/<时间>/` 再写入；新增的 skill 如果重名，会先确认是否覆盖。左栏「下载整个 skill 库」打包全部 skill。

会话保存在 `data/sessions/`，刷新页面或重启服务后可以继续。

## 配置项（.env）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容接口地址，以 `/v1` 结尾 | — |
| `LLM_API_KEY` | 接口密钥 | — |
| `LLM_MODEL` | 模型名 | — |
| `LLM_TEMPERATURE` / `LLM_TIMEOUT` / `LLM_MAX_TOKENS` | 采样温度 / 超时秒数 / 最大输出 token | 0.2 / 600 / 不传 |
| `SKILLS_DIR` | 本地 skill 库，每个 skill 一个目录，主文件 `SKILL.md` | `./skills` |
| `TEMPLATE_PATH` | skill 模板规范，直接修改该文件即可生效 | `./templates/skill_template.md` |
| `DATA_DIR` | 会话与备份目录 | `./data` |
| `HOST` / `PORT` | 监听地址 | `127.0.0.1` / `8000` |

## 模型输出协议

模型用下面的标记输出文件，后端解析后合并进草稿（没输出的文件保持不变）：

```
<<<FILE: SKILL.md>>>
...完整内容...
<<<END FILE>>>

<<<DELETE: reference/old.md>>>
```

历史轮次中的文件内容在发给模型时会被省略，每轮只附带当前最新草稿，避免上下文膨胀。

## 目录

```
app/
  main.py        FastAPI 路由（会话、上传、推荐、流式对话、diff、写回、下载）
  llm.py         OpenAI 兼容客户端（流式 / 非流式）
  prompts.py     系统提示、推荐提示、首轮 / 追问消息构造
  parser.py      Excel / CSV / Markdown 转 markdown
  fileblocks.py  多文件输出协议解析与路径安全校验
  lint.py        按模板对整个 skill 目录做格式检查
  skills.py      skill 库读写、备份、打包
  sessions.py    会话持久化
static/          前端（原生 HTML/JS/CSS，无需构建）
templates/skill_template.md
tests/           pytest；tests/mock_llm.py 是本地调试用的假模型接口
```

## 测试

```bash
python -m pytest -q
# 不连真实模型调试界面：
python -m tests.mock_llm 4099 &
LLM_BASE_URL=http://127.0.0.1:4099/v1 LLM_MODEL=mock python -m app
```
