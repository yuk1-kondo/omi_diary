"""
📔 GitHub日記アプリ

Omi会話データをGitHubリポジトリに自動保存する日記アプリです。
毎日の日記がMarkdownファイルとして保存されます！

構造:
diary/
  2025/
    01/
      01.md  ← 2025年1月1日の日記
      02.md
    02/
      01.md
"""

import os
import base64
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
import httpx

app = FastAPI(
    title="Omi GitHub日記",
    description="会話をGitHubに自動保存する日記アプリ",
    version="1.0.0"
)

# 環境変数
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Personal Access Token
GITHUB_REPO = os.getenv("GITHUB_REPO")    # 例: "username/omi-diary"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

GITHUB_API_URL = "https://api.github.com"
JST = timedelta(hours=9)  # 日本時間オフセット


def get_github_headers():
    """GitHub APIヘッダー"""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }


def get_diary_path(date: str) -> str:
    """日付から日記ファイルパスを生成"""
    # date: 2025-01-15 → diary/2025/01/15.md
    parts = date.split("-")
    return f"diary/{parts[0]}/{parts[1]}/{parts[2]}.md"


def get_transcript_path(date: str) -> str:
    """日付からSTT生テキストファイルパスを生成"""
    # date: 2025-01-15 → diary/2025/01/15_transcript.md
    parts = date.split("-")
    return f"diary/{parts[0]}/{parts[1]}/{parts[2]}_transcript.md"


def get_raw_data_path(date: str, conversation_id: str) -> str:
    """会話IDから生データJSONファイルパスを生成"""
    # date: 2025-01-15, conversation_id: abc123 → diary/2025/01/15/raw/abc123.json
    parts = date.split("-")
    return f"diary/{parts[0]}/{parts[1]}/{parts[2]}/raw/{conversation_id}.json"


async def get_file_content(path: str) -> Optional[dict]:
    """GitHubからファイル内容を取得"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/contents/{path}",
            headers=get_github_headers(),
            params={"ref": GITHUB_BRANCH}
        )
        
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return {
                "content": content,
                "sha": data["sha"]
            }
    return None


async def create_or_update_file(path: str, content: str, message: str, sha: Optional[str] = None) -> dict:
    """GitHubにファイルを作成または更新"""
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    data = {
        "message": message,
        "content": encoded_content,
        "branch": GITHUB_BRANCH
    }
    
    if sha:
        data["sha"] = sha
    
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/contents/{path}",
            headers=get_github_headers(),
            json=data
        )
        return response.json()


def generate_transcript_entry(conversation: dict) -> str:
    """会話データからSTT生テキストエントリを生成"""
    conversation_id = conversation.get("id", "unknown")
    created_at = conversation.get("created_at", "")
    
    # 時間を取得（JST）
    time_str = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt_jst = dt + JST
            time_str = dt_jst.strftime("%Y-%m-%d %H:%M:%S")
        except:
            time_str = (datetime.now(timezone.utc) + JST).strftime("%Y-%m-%d %H:%M:%S")
    
    structured = conversation.get("structured", {})
    title = structured.get("title", "会話")
    
    entry = f"""
## 📝 {title} - {conversation_id}

**記録時間**: {time_str}

### STT生テキスト

"""
    
    # transcript_segmentsを処理
    transcript_segments = conversation.get("transcript_segments", [])
    if transcript_segments:
        for segment in transcript_segments:
            text = segment.get("text", "").strip()
            speaker = segment.get("speaker", "SPEAKER_00")
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            is_user = segment.get("is_user", False)
            
            speaker_label = "👤 あなた" if is_user else f"🎤 {speaker}"
            timestamp = f"[{int(start)}s - {int(end)}s]"
            
            entry += f"{speaker_label} {timestamp}\n{text}\n\n"
    else:
        entry += "*STTデータがありません*\n\n"
    
    entry += "\n---\n\n"
    
    return entry


def generate_diary_entry(conversation: dict) -> str:
    """会話データから日記エントリを生成（Markdown形式）"""
    structured = conversation.get("structured", {})
    conversation_id = conversation.get("id", "")
    
    title = structured.get("title", "会話")
    overview = structured.get("overview", "")
    category = structured.get("category", "other")
    
    # 時間を取得（JST）
    created_at = conversation.get("created_at", "")
    time_str = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt_jst = dt + JST
            time_str = dt_jst.strftime("%H:%M")
        except:
            time_str = (datetime.now(timezone.utc) + JST).strftime("%H:%M")
    
    # カテゴリアイコン
    category_icons = {
        "personal": "👤", "education": "📚", "health": "🏥", "finance": "💰",
        "legal": "⚖️", "philosophy": "🤔", "spiritual": "🙏", "science": "🔬",
        "technology": "💻", "business": "💼", "social": "👥", "travel": "✈️",
        "food": "🍽️", "entertainment": "🎬", "sports": "⚽", "politics": "🏛️",
        "other": "💬"
    }
    icon = category_icons.get(category, "💬")
    
    # transcript_segmentsがあるかチェック
    transcript_segments = conversation.get("transcript_segments", [])
    has_transcript = len(transcript_segments) > 0
    
    # Markdownエントリを生成
    entry = f"""
### {icon} {title}

**時間**: {time_str}  
**カテゴリ**: {category}
"""
    
    # STT生テキストへのリンクを追加
    if has_transcript:
        entry += f"**📝 STT生テキスト**: [詳細を見る](#stt-{conversation_id[:8]})\n"
    
    entry += f"""
{overview}
"""
    
    # アクションアイテムがあれば追加
    action_items = structured.get("action_items", [])
    if action_items:
        entry += "\n**📋 アクションアイテム**:\n"
        for item in action_items[:5]:
            if isinstance(item, dict):
                entry += f"- [ ] {item.get('description', '')}\n"
            elif isinstance(item, str):
                entry += f"- [ ] {item}\n"
    
    entry += "\n---\n"
    
    return entry


def generate_diary_header(date: str) -> str:
    """日記ファイルのヘッダーを生成"""
    # 日付をパース
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
        formatted_date = dt.strftime(f"%Y年%m月%d日（{weekday_ja}）")
    except:
        formatted_date = date
    
    return f"""# 📔 {formatted_date} の日記

---

"""


@app.get("/", response_class=HTMLResponse)
async def root():
    """ホームページ"""
    configured = bool(GITHUB_TOKEN and GITHUB_REPO)
    status = "✅ GitHubに接続済み" if configured else "❌ GitHub未設定"
    repo_link = f"https://github.com/{GITHUB_REPO}" if GITHUB_REPO else "#"
    
    return f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>📔 Omi GitHub日記</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #24292e 0%, #1a1e22 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #fff;
            }}
            .container {{ text-align: center; padding: 40px; max-width: 500px; }}
            h1 {{ font-size: 3em; margin-bottom: 20px; }}
            .status {{
                padding: 15px 30px;
                background: rgba(255,255,255,0.1);
                border-radius: 10px;
                margin: 20px 0;
            }}
            .btn {{
                display: inline-block;
                margin: 10px;
                padding: 12px 24px;
                background: #238636;
                color: #fff;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
            }}
            .btn:hover {{ background: #2ea043; }}
            .btn-secondary {{
                background: rgba(255,255,255,0.1);
            }}
            .features {{
                margin-top: 40px;
                text-align: left;
            }}
            .feature {{
                display: flex;
                align-items: center;
                margin: 15px 0;
                padding: 15px;
                background: rgba(255,255,255,0.05);
                border-radius: 10px;
            }}
            .feature-icon {{ font-size: 1.5em; margin-right: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📔</h1>
            <h1>Omi GitHub日記</h1>
            <div class="status">{status}</div>
            <p>会話を自動でGitHubに保存</p>
            
            <div style="margin-top: 20px;">
                <a href="{repo_link}" class="btn" target="_blank">📁 リポジトリを見る</a>
                <a href="/test" class="btn btn-secondary">🔍 接続テスト</a>
            </div>
            
            <div class="features">
                <div class="feature">
                    <span class="feature-icon">📝</span>
                    <span>Markdownで日記を記録</span>
                </div>
                <div class="feature">
                    <span class="feature-icon">📅</span>
                    <span>日付ごとにファイルを自動作成</span>
                </div>
                <div class="feature">
                    <span class="feature-icon">🔄</span>
                    <span>バージョン管理で履歴を保存</span>
                </div>
                <div class="feature">
                    <span class="feature-icon">🆓</span>
                    <span>完全無料（Public リポジトリ）</span>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


@app.post("/webhook")
async def webhook(request: Request, uid: str = Query(None)):
    """Omi External Integrationからのwebhook"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return JSONResponse(
            status_code=500,
            content={"error": "GitHub設定がありません。GITHUB_TOKENとGITHUB_REPOを設定してください。"}
        )
    
    try:
        body = await request.json()
    except:
        return JSONResponse(status_code=400, content={"error": "無効なJSONデータ"})
    
    conversation = body if isinstance(body, dict) else {}
    
    # 日付を取得（JST）
    created_at = conversation.get("created_at", "")
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt_jst = dt + JST
            date = dt_jst.strftime("%Y-%m-%d")
        except:
            date = (datetime.now(timezone.utc) + JST).strftime("%Y-%m-%d")
    else:
        date = (datetime.now(timezone.utc) + JST).strftime("%Y-%m-%d")
    
    conversation_id = conversation.get("id", "")
    
    # 1. 通常の日記エントリを生成・保存
    diary_entry = generate_diary_entry(conversation)
    file_path = get_diary_path(date)
    existing = await get_file_content(file_path)
    
    if existing:
        new_content = existing["content"] + "\n" + diary_entry
        commit_message = f"📝 {date} の日記を更新"
        await create_or_update_file(file_path, new_content, commit_message, existing["sha"])
    else:
        header = generate_diary_header(date)
        new_content = header + diary_entry
        commit_message = f"📔 {date} の日記を作成"
        await create_or_update_file(file_path, new_content, commit_message)
    
    # 2. STT生テキストを保存（transcript_segmentsがある場合）
    transcript_segments = conversation.get("transcript_segments", [])
    transcript_url = None
    raw_data_url = None
    
    if transcript_segments:
        # STT生テキストファイルに追記
        transcript_path = get_transcript_path(date)
        transcript_entry = generate_transcript_entry(conversation)
        existing_transcript = await get_file_content(transcript_path)
        
        if existing_transcript:
            new_transcript_content = existing_transcript["content"] + "\n" + transcript_entry
            commit_message_transcript = f"📝 {date} のSTT生テキストを更新"
            await create_or_update_file(
                transcript_path, 
                new_transcript_content, 
                commit_message_transcript, 
                existing_transcript["sha"]
            )
        else:
            # 新しいSTTファイルを作成
            transcript_header = f"# 📝 {date} のSTT生テキスト\n\n---\n\n"
            new_transcript_content = transcript_header + transcript_entry
            commit_message_transcript = f"📝 {date} のSTT生テキストを作成"
            await create_or_update_file(transcript_path, new_transcript_content, commit_message_transcript)
        
        transcript_url = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{transcript_path}"
    
    # 3. 生データJSONを保存（オプション：会話全体のJSON）
    if conversation_id:
        raw_data_path = get_raw_data_path(date, conversation_id)
        raw_data_json = json.dumps(conversation, ensure_ascii=False, indent=2, default=str)
        commit_message_raw = f"💾 {date} の会話生データを保存: {conversation_id[:8]}"
        await create_or_update_file(raw_data_path, raw_data_json, commit_message_raw)
        raw_data_url = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{raw_data_path}"
    
    message = f"📔 {date} の日記を保存しました！"
    if transcript_url:
        message += " STT生テキストも保存済み。"
    
    return {
        "message": message,
        "date": date,
        "file_path": file_path,
        "github_url": f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{file_path}",
        "transcript_url": transcript_url,
        "raw_data_url": raw_data_url,
        "has_transcript": len(transcript_segments) > 0
    }


@app.get("/test")
async def test_github():
    """GitHub接続テスト"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {
            "status": "error",
            "message": "GitHub設定がありません",
            "github_token": "未設定" if not GITHUB_TOKEN else "設定済み",
            "github_repo": "未設定" if not GITHUB_REPO else GITHUB_REPO
        }
    
    # リポジトリにアクセスできるか確認
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_REPO}",
            headers=get_github_headers()
        )
        
        if response.status_code == 200:
            repo_info = response.json()
            return {
                "status": "ok",
                "message": "✅ GitHubに正常に接続できました！",
                "repository": repo_info.get("full_name"),
                "private": repo_info.get("private"),
                "url": repo_info.get("html_url")
            }
        else:
            return {
                "status": "error",
                "message": f"❌ GitHubに接続できませんでした: {response.status_code}",
                "detail": response.json()
            }


@app.get("/diary/{date}")
async def get_diary(date: str):
    """指定された日付の日記を取得"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return JSONResponse(status_code=500, content={"error": "GitHub設定がありません"})
    
    file_path = get_diary_path(date)
    existing = await get_file_content(file_path)
    
    if existing:
        return {
            "date": date,
            "content": existing["content"],
            "github_url": f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{file_path}"
        }
    else:
        return JSONResponse(
            status_code=404,
            content={"message": f"{date} の日記はまだありません"}
        )


@app.get("/health")
async def health():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "github_configured": bool(GITHUB_TOKEN and GITHUB_REPO),
        "repository": GITHUB_REPO,
        "version": "1.0.0"
    }


# Vercel用（最新のVercelでは不要な場合があります）
# handler = app




