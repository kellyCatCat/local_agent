"""端到端：上传 → 推荐 → 选择目标 → 生成（流式）→ 追问 → diff → 写回 → 下载。模型调用被替换为假实现。"""
import importlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.conftest import fixture, multi_files


@pytest.fixture
def env(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    (skills_dir / "srv6-te-policy-down").mkdir(parents=True)
    (skills_dir / "srv6-te-policy-down" / "SKILL.md").write_text(fixture("single_skill.md"), "utf-8")
    (skills_dir / "srv6-te-policy-down" / "logo.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("SKILLS_DIR", str(skills_dir))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_BASE_URL", "http://fake/v1")
    monkeypatch.setenv("LLM_MODEL", "fake")
    import app.config
    import app.llm
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.llm)
    main = importlib.reload(app.main)

    calls = []
    replies = []

    async def fake_complete(messages):
        calls.append(messages)
        return 'blah {"action": "modify", "target": "srv6-te-policy-down", "reason": "同一故障现象", "candidates": []}'

    async def fake_stream(messages):
        calls.append(messages)
        text = replies.pop(0)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]

    monkeypatch.setattr(main.llm, "complete", fake_complete)
    monkeypatch.setattr(main.llm, "stream", fake_stream)
    return TestClient(main.app), skills_dir, calls, replies


def sse(resp):
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]


def blocks(files):
    return "\n".join(f"<<<FILE: {p}>>>\n{c}<<<END FILE>>>" for p, c in files.items())


def test_full_flow(env):
    client, skills_dir, calls, replies = env
    sid = client.post("/api/sessions").json()["id"]

    r = client.post(f"/api/sessions/{sid}/uploads", files=[("files", ("补充.md", "子场景：邻居震荡".encode(), "text/markdown"))])
    assert r.status_code == 200 and r.json()["session"]["uploads"][0]["name"] == "补充.md"

    rec = client.post(f"/api/sessions/{sid}/recommend").json()["recommendation"]
    assert rec["action"] == "modify" and rec["target"] == "srv6-te-policy-down"

    v = client.post(f"/api/sessions/{sid}/target", json={"mode": "modify", "target": "srv6-te-policy-down"}).json()
    assert v["draft"]["version"] == 1 and "SKILL.md" in v["draft"]["files"]

    # 第一轮：重组为多场景
    new_files = multi_files()
    replies.append("改为多场景布局。\n" + blocks(new_files))
    events = sse(client.post(f"/api/sessions/{sid}/chat", json={"message": ""}))
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    done = events[-1]
    assert done["version"] == 2
    draft = done["session"]["draft"]
    assert set(draft["files"]) == set(new_files)
    assert draft["lint"] == []
    first_prompt = calls[-1][-1]["content"]
    assert "子场景：邻居震荡" in first_prompt and "已有 skill 文件" in first_prompt
    assert "产出 skill 的模板规范" in calls[-1][0]["content"]

    # 追问：只改一个文件并删除另一个
    ref = new_files["reference/neighbor-down.md"].replace("接口 Down", "接口物理 Down")
    replies.append("已修改。\n" + blocks({"reference/neighbor-down.md": ref}) + "\n<<<DELETE: reference/neighbor-flap.md>>>\n")
    done = sse(client.post(f"/api/sessions/{sid}/chat", json={"message": "改根因名"}))[-1]
    files = done["session"]["draft"]["files"]
    assert "reference/neighbor-flap.md" not in files and "接口物理 Down" in files["reference/neighbor-down.md"]
    assert any("neighbor-flap" in i["message"] for i in done["session"]["draft"]["lint"])
    followup = calls[-1]
    assert "当前 skill 最新版本" in followup[-1]["content"]
    assert "<<<FILE:" not in followup[-2]["content"]  # 历史回复里的文件块已省略

    # 纯提问：不产生新版本
    replies.append("这是解释，没有改动。")
    done = sse(client.post(f"/api/sessions/{sid}/chat", json={"message": "为什么这么改？"}))[-1]
    assert done["version"] is None

    diff = client.get(f"/api/sessions/{sid}/diff").json()["files"]
    assert {f["path"] for f in diff} == {"SKILL.md", "reference/neighbor-down.md"}

    # 手动编辑 + 回退
    s = client.put(f"/api/sessions/{sid}/draft", json={"files": {**files, "SKILL.md": files["SKILL.md"] + "\n补充\n"}}).json()
    assert s["draft"]["version"] == 4
    s = client.post(f"/api/sessions/{sid}/draft/revert", json={"version": 3}).json()
    assert s["draft"]["version"] == 5 and s["draft"]["files"] == files

    # 下载草稿：带上目录里的非文本文件
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/sessions/{sid}/draft/download").content))
    assert set(z.namelist()) == {"srv6-te-policy-down/SKILL.md", "srv6-te-policy-down/reference/neighbor-down.md", "srv6-te-policy-down/logo.png"}

    # 写回：先备份，删除原 SKILL.md 之外不需要的文件，保留 png
    r = client.post(f"/api/sessions/{sid}/writeback", json={}).json()
    d = skills_dir / "srv6-te-policy-down"
    assert (d / "reference" / "neighbor-down.md").is_file() and (d / "logo.png").is_file()
    assert "isis-troubleshooting" in (d / "SKILL.md").read_text("utf-8")
    assert "srv6-te-policy-down" in r["backup"] and "SRv6 TE Policy" in open(r["backup"] + "/SKILL.md", encoding="utf-8").read()
    assert r["session"]["draft"]["changed"] == {}

    lib = zipfile.ZipFile(io.BytesIO(client.get("/api/library/download").content))
    assert "srv6-te-policy-down/reference/neighbor-down.md" in lib.namelist()

    # 会话持久化
    assert client.get(f"/api/sessions/{sid}").json()["draft"]["version"] == 5


def test_create_flow_and_conflict(env):
    client, skills_dir, calls, replies = env
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/uploads", files=[("files", ("a.md", b"x", "text/markdown"))])
    assert client.post(f"/api/sessions/{sid}/target", json={"mode": "create", "name": "Bad Name"}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/target", json={"mode": "create", "name": "srv6-te-policy-down"}).status_code == 409
    client.post(f"/api/sessions/{sid}/target", json={"mode": "create", "name": "isis-troubleshooting"})

    replies.append(blocks(multi_files()))
    sse(client.post(f"/api/sessions/{sid}/chat", json={"message": "生成"}))
    assert "isis-troubleshooting" in calls[-1][-1]["content"]
    assert client.post(f"/api/sessions/{sid}/target", json={"mode": "create"}).status_code == 409

    r = client.post(f"/api/sessions/{sid}/writeback", json={})
    assert r.status_code == 200 and r.json()["backup"] is None
    assert (skills_dir / "isis-troubleshooting" / "reference" / "neighbor-flap.md").is_file()
    assert r.json()["session"]["mode"] == "modify"


def test_llm_error_not_saved(env, monkeypatch):
    client, _, _, _ = env
    import app.main as main

    async def boom(messages):
        raise main.llm.LLMError("连接失败")
        yield ""

    monkeypatch.setattr(main.llm, "stream", boom)
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/uploads", files=[("files", ("a.md", b"x", "text/markdown"))])
    client.post(f"/api/sessions/{sid}/target", json={"mode": "create"})
    ev = sse(client.post(f"/api/sessions/{sid}/chat", json={"message": ""}))
    assert ev[-1] == {"type": "error", "message": "连接失败"}
    assert client.get(f"/api/sessions/{sid}").json()["messages"] == []


def test_bad_session_id(env):
    client = env[0]
    assert client.get("/api/sessions/../../etc").status_code == 404
    assert client.get("/api/sessions/" + "a" * 32).status_code == 404
    assert client.get("/api/skills/..%2F..%2Fetc").status_code == 404
