"""本地调试用的假 OpenAI 兼容接口：python -m tests.mock_llm（默认端口 4099）。

- 推荐请求（提示中含"只输出 JSON"）返回固定 JSON；
- 其余请求流式返回 tests/fixtures 中的多场景 skill。
"""
import asyncio
import json
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from tests.conftest import multi_files

app = FastAPI()


def reply_for(messages) -> str:
    last = messages[-1]["content"]
    if "只输出 JSON" in messages[0]["content"]:
        return json.dumps({"action": "create", "target": None, "suggested_name": "isis-troubleshooting",
                           "candidates": [], "reason": "源文档描述 IS-IS 邻居类故障，库中没有对应 skill"}, ensure_ascii=False)
    if "为什么" in last:
        return "因为源文档覆盖两个故障场景，按模板拆成 reference/ 下的两个场景文件。"
    files = multi_files()
    body = "\n".join(f"<<<FILE: {p}>>>\n{c}<<<END FILE>>>" for p, c in files.items())
    return "源文档覆盖 2 个故障场景，按多场景布局生成：\n- 场景A：邻居无法建立\n- 场景B：邻居震荡\n\n" + body


@app.post("/v1/chat/completions")
async def chat(req: Request):
    data = await req.json()
    text = reply_for(data["messages"])
    if not data.get("stream"):
        return {"choices": [{"message": {"role": "assistant", "content": text}}]}

    async def gen():
        for i in range(0, len(text), 24):
            chunk = {"choices": [{"delta": {"content": text[i:i + 24]}}]}
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.005)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 4099)
