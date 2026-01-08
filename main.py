"""
📔 GitHub日記アプリ（リファクタリング版）

Omi会話データをGitHubリポジトリに自動保存する日記アプリです。
毎日の日記がMarkdownファイルとして保存されます！

改善点:
- 日時変換の共通化
- HTTPクライアントの再利用
- エラーハンドリングの改善
- 設定の一元管理
- HTMLテンプレートの分離
"""

import os
import base64
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo  # Python 3.9+ 標準ライブラリ
from typing import Optional, Dict, Any
from dataclasses import dataclass
from functools import lru_cache
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import httpx

# ============================================
# 設定管理
# ============================================

@dataclass(frozen=True)
class Config:
    """アプリケーション設定"""
    github_token: str
    github_repo: str
    github_branch: str = "main"
    github_api_url: str = "https://api.github.com"
    timezone: str = "Asia/Tokyo"
    
    @property
    def is_configured(self) -> bool:
        return bool(self.github_token and self.github_repo)
    
    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_repo=os.getenv("GITHUB_REPO", ""),
            github_branch=os.getenv("GITHUB_BRANCH", "main"),
        )


@lru_cache()
def get_config() -> Config:
    """設定を取得（キャッシュ付き）"""
    return Config.from_env()


# ============================================
# ロギング設定
# ============================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================
# カテゴリアイコン定数
# ============================================

CATEGORY_ICONS: Dict[str, str] = {
    "personal": "👤", "education": "📚", "health": "🏥", "finance": "💰",
    "legal": "⚖️", "philosophy": "🤔", "spiritual": "🙏", "science": "🔬",
    "technology": "💻", "business": "💼", "social": "👥", "travel": "✈️",
    "food": "🍽️", "entertainment": "🎬", "sports": "⚽", "politics": "🏛️",
    "other": "💬"
}

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


# ============================================
# 日時ユーティリティ
# ============================================

class DateTimeHelper:
    """日時変換ヘルパー"""
    
    def __init__(self, tz_name: str = "Asia/Tokyo"):
        self.tz = ZoneInfo(tz_name)
    
    def parse_iso(self, iso_string: str) -> datetime:
        """ISO形式の日時文字列をパース"""
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return dt.astimezone(self.tz)
        except (ValueError, TypeError):
            return datetime.now(self.tz)
    
    def now(self) -> datetime:
        """現在時刻を取得"""
        return datetime.now(self.tz)
    
    def format_date(self, dt: datetime) -> str:
        """日付をYYYY-MM-DD形式でフォーマット"""
        return dt.strftime("%Y-%m-%d")
    
    def format_time(self, dt: datetime) -> str:
        """時刻をHH:MM形式でフォーマット"""
        return dt.strftime("%H:%M")
    
    def format_datetime(self, dt: datetime) -> str:
        """日時をフルフォーマット"""
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    
    def format_date_ja(self, dt: datetime) -> str:
        """日付を日本語形式でフォーマット"""
        weekday = WEEKDAY_JA[dt.weekday()]
        return dt.strftime(f"%Y年%m月%d日（{weekday}）")


# ============================================
# パス生成
# ============================================

class PathGenerator:
    """ファイルパス生成"""
    
    @staticmethod
    def _date_parts(date: str) -> tuple:
        """日付を年/月/日に分割"""
        return tuple(date.split("-"))
    
    @classmethod
    def diary(cls, date: str) -> str:
        """日記ファイルパス"""
        year, month, day = cls._date_parts(date)
        return f"diary/{year}/{month}/{day}.md"
    
    @classmethod
    def transcript(cls, date: str) -> str:
        """STT生テキストファイルパス"""
        year, month, day = cls._date_parts(date)
        return f"diary/{year}/{month}/{day}_transcript.md"
    
    @classmethod
    def raw_data(cls, date: str, conversation_id: str) -> str:
        """生データJSONファイルパス"""
        year, month, day = cls._date_parts(date)
        return f"diary/{year}/{month}/{day}/raw/{conversation_id}.json"


# ============================================
# GitHub APIクライアント
# ============================================

class GitHubClient:
    """GitHub API操作クラス"""
    
    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    
    async def _get_client(self) -> httpx.AsyncClient:
        """HTTPクライアントを取得（再利用）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=30.0
            )
        return self._client
    
    async def close(self):
        """クライアントをクローズ"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        """ファイル内容を取得"""
        client = await self._get_client()
        url = f"{self.config.github_api_url}/repos/{self.config.github_repo}/contents/{path}"
        
        try:
            response = await client.get(url, params={"ref": self.config.github_branch})
            if response.status_code == 200:
                data = response.json()
                return {
                    "content": base64.b64decode(data["content"]).decode("utf-8"),
                    "sha": data["sha"]
                }
            elif response.status_code == 404:
                return None
            else:
                logger.warning(f"GitHub API error: {response.status_code} for {path}")
                return None
        except httpx.HTTPError as e:
            logger.error(f"HTTP error getting file {path}: {e}")
            return None
    
    async def put_file(
        self, 
        path: str, 
        content: str, 
        message: str, 
        sha: Optional[str] = None
    ) -> Dict[str, Any]:
        """ファイルを作成または更新"""
        client = await self._get_client()
        url = f"{self.config.github_api_url}/repos/{self.config.github_repo}/contents/{path}"
        
        data = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": self.config.github_branch
        }
        if sha:
            data["sha"] = sha
        
        try:
            response = await client.put(url, json=data)
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error putting file {path}: {e}")
            raise
    
    async def get_repo_info(self) -> Optional[Dict[str, Any]]:
        """リポジトリ情報を取得"""
        client = await self._get_client()
        url = f"{self.config.github_api_url}/repos/{self.config.github_repo}"
        
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            return None
        except httpx.HTTPError as e:
            logger.error(f"HTTP error getting repo info: {e}")
            return None


# ============================================
# コンテンツ生成
# ============================================

class ContentGenerator:
    """Markdownコンテンツ生成"""
    
    def __init__(self, dt_helper: DateTimeHelper):
        self.dt_helper = dt_helper
    
    def diary_header(self, date: str) -> str:
        """日記ファイルのヘッダー"""
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            formatted = self.dt_helper.format_date_ja(dt)
        except ValueError:
            formatted = date
        
        return f"# 📔 {formatted} の日記\n\n---\n\n"
    
    def diary_entry(self, conversation: Dict[str, Any]) -> str:
        """日記エントリを生成"""
        structured = conversation.get("structured", {})
        conversation_id = conversation.get("id", "")
        
        title = structured.get("title", "会話")
        overview = structured.get("overview", "")
        category = structured.get("category", "other")
        
        # 時間
        created_at = conversation.get("created_at", "")
        dt = self.dt_helper.parse_iso(created_at) if created_at else self.dt_helper.now()
        time_str = self.dt_helper.format_time(dt)
        
        # アイコン
        icon = CATEGORY_ICONS.get(category, "💬")
        
        # トランスクリプト有無
        has_transcript = bool(conversation.get("transcript_segments"))
        
        entry = f"\n### {icon} {title}\n\n"
        entry += f"**時間**: {time_str}  \n"
        entry += f"**カテゴリ**: {category}\n"
        
        if has_transcript and conversation_id:
            entry += f"**📝 STT生テキスト**: [詳細を見る](#stt-{conversation_id[:8]})\n"
        
        entry += f"\n{overview}\n"
        
        # アクションアイテム
        action_items = structured.get("action_items", [])
        if action_items:
            entry += "\n**📋 アクションアイテム**:\n"
            for item in action_items[:5]:
                desc = item.get("description", "") if isinstance(item, dict) else str(item)
                entry += f"- [ ] {desc}\n"
        
        entry += "\n---\n"
        return entry
    
    def transcript_header(self, date: str) -> str:
        """STT生テキストファイルのヘッダー"""
        return f"# 📝 {date} のSTT生テキスト\n\n---\n\n"
    
    def transcript_entry(self, conversation: Dict[str, Any]) -> str:
        """STT生テキストエントリを生成"""
        conversation_id = conversation.get("id", "unknown")
        created_at = conversation.get("created_at", "")
        structured = conversation.get("structured", {})
        title = structured.get("title", "会話")
        
        dt = self.dt_helper.parse_iso(created_at) if created_at else self.dt_helper.now()
        time_str = self.dt_helper.format_datetime(dt)
        
        entry = f"\n## 📝 {title} - {conversation_id}\n\n"
        entry += f"**記録時間**: {time_str}\n\n"
        entry += "### STT生テキスト\n\n"
        
        segments = conversation.get("transcript_segments", [])
        if segments:
            for seg in segments:
                text = seg.get("text", "").strip()
                speaker = seg.get("speaker", "SPEAKER_00")
                start = int(seg.get("start", 0))
                end = int(seg.get("end", 0))
                is_user = seg.get("is_user", False)
                
                label = "👤 あなた" if is_user else f"🎤 {speaker}"
                entry += f"{label} [{start}s - {end}s]\n{text}\n\n"
        else:
            entry += "*STTデータがありません*\n\n"
        
        entry += "\n---\n\n"
        return entry


# ============================================
# ファイル保存サービス
# ============================================

class DiaryService:
    """日記保存サービス"""
    
    def __init__(self, github: GitHubClient, generator: ContentGenerator):
        self.github = github
        self.generator = generator
    
    async def save_or_append(
        self,
        path: str,
        content: str,
        header: str,
        commit_message_new: str,
        commit_message_update: str
    ) -> str:
        """ファイルを新規作成または追記"""
        existing = await self.github.get_file(path)
        
        if existing:
            new_content = existing["content"] + "\n" + content
            await self.github.put_file(path, new_content, commit_message_update, existing["sha"])
            return "updated"
        else:
            new_content = header + content
            await self.github.put_file(path, new_content, commit_message_new)
            return "created"
    
    async def save_conversation(self, conversation: Dict[str, Any], date: str) -> Dict[str, Any]:
        """会話を保存"""
        config = get_config()
        conversation_id = conversation.get("id", "")
        transcript_segments = conversation.get("transcript_segments", [])
        
        result = {
            "date": date,
            "diary_path": PathGenerator.diary(date),
            "transcript_path": None,
            "raw_data_path": None,
        }
        
        # 1. 日記を保存
        diary_entry = self.generator.diary_entry(conversation)
        await self.save_or_append(
            path=result["diary_path"],
            content=diary_entry,
            header=self.generator.diary_header(date),
            commit_message_new=f"📔 {date} の日記を作成",
            commit_message_update=f"📝 {date} の日記を更新"
        )
        
        # 2. STT生テキストを保存
        if transcript_segments:
            result["transcript_path"] = PathGenerator.transcript(date)
            transcript_entry = self.generator.transcript_entry(conversation)
            await self.save_or_append(
                path=result["transcript_path"],
                content=transcript_entry,
                header=self.generator.transcript_header(date),
                commit_message_new=f"📝 {date} のSTT生テキストを作成",
                commit_message_update=f"📝 {date} のSTT生テキストを更新"
            )
        
        # 3. 生データJSONを保存
        if conversation_id:
            result["raw_data_path"] = PathGenerator.raw_data(date, conversation_id)
            raw_json = json.dumps(conversation, ensure_ascii=False, indent=2, default=str)
            await self.github.put_file(
                result["raw_data_path"],
                raw_json,
                f"💾 {date} の会話生データを保存: {conversation_id[:8]}"
            )
        
        return result


# ============================================
# HTMLテンプレート
# ============================================

def render_home_page(config: Config) -> str:
    """ホームページHTML"""
    status = "✅ GitHubに接続済み" if config.is_configured else "❌ GitHub未設定"
    repo_link = f"https://github.com/{config.github_repo}" if config.github_repo else "#"
    
    return f"""<!DOCTYPE html>
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
        .status {{ padding: 15px 30px; background: rgba(255,255,255,0.1); border-radius: 10px; margin: 20px 0; }}
        .btn {{ display: inline-block; margin: 10px; padding: 12px 24px; background: #238636; color: #fff; text-decoration: none; border-radius: 6px; font-weight: bold; }}
        .btn:hover {{ background: #2ea043; }}
        .btn-secondary {{ background: rgba(255,255,255,0.1); }}
        .features {{ margin-top: 40px; text-align: left; }}
        .feature {{ display: flex; align-items: center; margin: 15px 0; padding: 15px; background: rgba(255,255,255,0.05); border-radius: 10px; }}
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
            <div class="feature"><span class="feature-icon">📝</span><span>Markdownで日記を記録</span></div>
            <div class="feature"><span class="feature-icon">📅</span><span>日付ごとにファイルを自動作成</span></div>
            <div class="feature"><span class="feature-icon">🔄</span><span>バージョン管理で履歴を保存</span></div>
            <div class="feature"><span class="feature-icon">🆓</span><span>完全無料（Public リポジトリ）</span></div>
        </div>
    </div>
</body>
</html>"""


# ============================================
# 依存性注入
# ============================================

async def get_github_client() -> GitHubClient:
    """GitHubクライアントを取得"""
    return GitHubClient(get_config())


async def get_diary_service() -> DiaryService:
    """日記サービスを取得"""
    config = get_config()
    github = GitHubClient(config)
    dt_helper = DateTimeHelper(config.timezone)
    generator = ContentGenerator(dt_helper)
    return DiaryService(github, generator)


def require_config(config: Config = Depends(get_config)) -> Config:
    """設定が有効か検証"""
    if not config.is_configured:
        raise HTTPException(
            status_code=500,
            detail="GitHub設定がありません。GITHUB_TOKENとGITHUB_REPOを設定してください。"
        )
    return config


# ============================================
# FastAPIアプリケーション
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル"""
    logger.info("Starting Omi GitHub Diary App")
    yield
    logger.info("Shutting down Omi GitHub Diary App")


app = FastAPI(
    title="Omi GitHub日記",
    description="会話をGitHubに自動保存する日記アプリ",
    version="2.0.0",
    lifespan=lifespan
)


# ============================================
# エンドポイント
# ============================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """ホームページ"""
    return render_home_page(get_config())


@app.post("/webhook")
async def webhook(
    request: Request,
    uid: str = Query(None),
    config: Config = Depends(require_config)
):
    """Omi External Integrationからのwebhook"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="無効なJSONデータ")
    
    conversation = body if isinstance(body, dict) else {}
    
    # 日付を取得
    dt_helper = DateTimeHelper(config.timezone)
    created_at = conversation.get("created_at", "")
    dt = dt_helper.parse_iso(created_at) if created_at else dt_helper.now()
    date = dt_helper.format_date(dt)
    
    # 保存
    service = await get_diary_service()
    result = await service.save_conversation(conversation, date)
    
    # URLを生成
    base_url = f"https://github.com/{config.github_repo}/blob/{config.github_branch}"
    
    message = f"📔 {date} の日記を保存しました！"
    if result["transcript_path"]:
        message += " STT生テキストも保存済み。"
    
    return {
        "message": message,
        "date": date,
        "file_path": result["diary_path"],
        "github_url": f"{base_url}/{result['diary_path']}",
        "transcript_url": f"{base_url}/{result['transcript_path']}" if result["transcript_path"] else None,
        "raw_data_url": f"{base_url}/{result['raw_data_path']}" if result["raw_data_path"] else None,
        "has_transcript": result["transcript_path"] is not None
    }


@app.get("/test")
async def test_github(config: Config = Depends(get_config)):
    """GitHub接続テスト"""
    if not config.is_configured:
        return {
            "status": "error",
            "message": "GitHub設定がありません",
            "github_token": "未設定" if not config.github_token else "設定済み",
            "github_repo": config.github_repo or "未設定"
        }
    
    github = GitHubClient(config)
    try:
        repo_info = await github.get_repo_info()
        if repo_info:
            return {
                "status": "ok",
                "message": "✅ GitHubに正常に接続できました！",
                "repository": repo_info.get("full_name"),
                "private": repo_info.get("private"),
                "url": repo_info.get("html_url")
            }
        return {
            "status": "error",
            "message": "❌ GitHubに接続できませんでした"
        }
    finally:
        await github.close()


@app.get("/diary/{date}")
async def get_diary(date: str, config: Config = Depends(require_config)):
    """指定された日付の日記を取得"""
    github = GitHubClient(config)
    try:
        file_path = PathGenerator.diary(date)
        existing = await github.get_file(file_path)
        
        if existing:
            return {
                "date": date,
                "content": existing["content"],
                "github_url": f"https://github.com/{config.github_repo}/blob/{config.github_branch}/{file_path}"
            }
        raise HTTPException(status_code=404, detail=f"{date} の日記はまだありません")
    finally:
        await github.close()


@app.get("/health")
async def health():
    """ヘルスチェック"""
    config = get_config()
    return {
        "status": "ok",
        "github_configured": config.is_configured,
        "repository": config.github_repo,
        "version": "2.0.0"
    }
