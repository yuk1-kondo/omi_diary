import os
import base64
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
import httpx

app = FastAPI(title="Omi GitHub日記", version="1.0.0")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_API_URL = "https://api.github.com"

def get_github_headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

def get_diary_path(date: str) -> str:
    parts = date.split("-")
    return f"diary/{parts[0]}/{parts[1]}/{parts[2]}.md"

async def get_file_content(path: str) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/contents/{path}", headers=get_github_headers(), params={"ref": GITHUB_BRANCH})
        if response.status_code == 200:
            data = response.json()
            return {"content": base64.b64decode(data["content"]).decode("utf-8"), "sha": data["sha"]}
    return None

async def create_or_update_file(path: str, content: str, message: str, sha: Optional[str] = None) -> dict:
    data = {"message": message, "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"), "branch": GITHUB_BRANCH}
    if sha:
        data["sha"] = sha
    async with httpx.AsyncClient() as client:
        return (await client.put(f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/contents/{path}", headers=get_github_headers(), json=data)).json()

def generate_diary_entry(conversation: dict) -> str:
    structured = conversation.get("structured", {})
    title, overview, category = structured.get("title", "会話"), structured.get("overview", ""), structured.get("category", "other")
    created_at = conversation.get("created_at", "")
    time_str = ""
    if created_at:
        try:
            time_str = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%H:%M")
        except:
            time_str = datetime.now(timezone.utc).strftime("%H:%M")
    icons = {"personal": "👤", "education": "📚", "health": "🏥", "finance": "💰", "technology": "💻", "business": "💼", "social": "👥", "travel": "✈️", "food": "🍽️", "entertainment": "🎬", "other": "💬"}
    entry = f"\n### {icons.get(category, '💬')} {title}\n\n**時間**: {time_str}  \n**カテゴリ**: {category}\n\n{overview}\n"
    action_items = structured.get("action_items", [])
    if action_items:
        entry += "\n**📋 アクションアイテム**:\n"
        for item in action_items[:5]:
            entry += f"- [ ] {item.get('description', '') if isinstance(item, dict) else item}\n"
    entry += "\n---\n"
    return entry

def generate_diary_header(date: str) -> str:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
        formatted = dt.strftime(f"%Y年%m月%d日（{weekday_ja}）")
    except:
        formatted = date
    return f"# 📔 {formatted} の日記\n\n---\n\n"

@app.get("/", response_class=HTMLResponse)
async def root():
    configured = bool(GITHUB_TOKEN and GITHUB_REPO)
    status = "✅ GitHubに接続済み" if configured else "❌ GitHub未設定"
    repo_link = f"https://github.com/{GITHUB_REPO}" if GITHUB_REPO else "#"
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Omi GitHub日記</title><style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:linear-gradient(135deg,#24292e,#1a1e22);min-height:100vh;display:flex;align-items:center;justify-content:center;color:#fff}}.container{{text-align:center;padding:40px;max-width:500px}}h1{{font-size:3em;margin-bottom:20px}}.status{{padding:15px 30px;background:rgba(255,255,255,0.1);border-radius:10px;margin:20px 0}}.btn{{display:inline-block;margin:10px;padding:12px 24px;background:#238636;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold}}.features{{margin-top:40px;text-align:left}}.feature{{display:flex;align-items:center;margin:15px 0;padding:15px;background:rgba(255,255,255,0.05);border-radius:10px}}.feature-icon{{font-size:1.5em;margin-right:15px}}</style></head><body><div class="container"><h1>📔</h1><h1>Omi GitHub日記</h1><div class="status">{status}</div><p>会話を自動でGitHubに保存</p><div style="margin-top:20px"><a href="{repo_link}" class="btn" target="_blank">📁 リポジトリを見る</a><a href="/test" class="btn" style="background:rgba(255,255,255,0.1)">🔍 接続テスト</a></div><div class="features"><div class="feature"><span class="feature-icon">📝</span><span>Markdownで日記を記録</span></div><div class="feature"><span class="feature-icon">📅</span><span>日付ごとにファイルを自動作成</span></div><div class="feature"><span class="feature-icon">🔄</span><span>バージョン管理で履歴を保存</span></div></div></div></body></html>"""

@app.post("/webhook")
async def webhook(request: Request, uid: str = Query(None)):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return JSONResponse(status_code=500, content={"error": "GitHub設定がありません"})
    try:
        body = await request.json()
    except:
        return JSONResponse(status_code=400, content={"error": "無効なデータ"})
    conversation = body if isinstance(body, dict) else {}
    created_at = conversation.get("created_at", "")
    try:
        date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y-%m-%d") if created_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    diary_entry = generate_diary_entry(conversation)
    file_path = get_diary_path(date)
    existing = await get_file_content(file_path)
    if existing:
        new_content = existing["content"] + "\n" + diary_entry
        await create_or_update_file(file_path, new_content, f"📝 {date} の日記を更新", existing["sha"])
        message = f"📔 {date} の日記を更新しました！"
    else:
        new_content = generate_diary_header(date) + diary_entry
        await create_or_update_file(file_path, new_content, f"📔 {date} の日記を作成")
        message = f"📔 {date} の日記を作成しました！"
    return {"message": message, "date": date, "file_path": file_path, "github_url": f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{file_path}"}

@app.get("/test")
async def test():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {"status": "error", "message": "GitHub設定がありません"}
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{GITHUB_API_URL}/repos/{GITHUB_REPO}", headers=get_github_headers())
        if response.status_code == 200:
            return {"status": "ok", "message": "✅ GitHubに正常に接続できました！", "repository": response.json().get("full_name")}
        return {"status": "error", "message": f"❌ 接続失敗: {response.status_code}"}

@app.get("/health")
async def health():
    return {"status": "ok", "github_configured": bool(GITHUB_TOKEN and GITHUB_REPO), "repository": GITHUB_REPO}

handler = app
