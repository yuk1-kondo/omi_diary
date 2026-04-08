"""
リファクタリング検証テスト

main.py のリファクタリング後のコードが正しく動作することを検証します。
"""

import os
import json
import base64
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import httpx


# ============================================
# テスト対象のインポート
# ============================================

from main import (
    Config,
    DateTimeHelper,
    PathGenerator,
    ContentGenerator,
    DiaryService,
    GitHubClient,
    get_config,
    require_config,
    app,
    CATEGORY_ICONS,
    WEEKDAY_JA,
    render_home_page,
)


# ============================================
# Config テスト
# ============================================

class TestConfig:
    def test_from_env_defaults(self):
        """環境変数なしのデフォルト値"""
        with patch.dict(os.environ, {}, clear=True):
            config = Config.from_env()
        assert config.github_token == ""
        assert config.github_repo == ""
        assert config.github_branch == "main"

    def test_from_env_with_values(self):
        """環境変数が設定されている場合"""
        with patch.dict(os.environ, {
            "GITHUB_TOKEN": "test_token",
            "GITHUB_REPO": "user/repo",
            "GITHUB_BRANCH": "develop",
        }):
            config = Config.from_env()
        assert config.github_token == "test_token"
        assert config.github_repo == "user/repo"
        assert config.github_branch == "develop"

    def test_is_configured_true(self):
        """トークンとリポジトリが両方設定されている場合"""
        config = Config(github_token="token", github_repo="user/repo")
        assert config.is_configured is True

    def test_is_configured_false_no_token(self):
        """トークンがない場合"""
        config = Config(github_token="", github_repo="user/repo")
        assert config.is_configured is False

    def test_is_configured_false_no_repo(self):
        """リポジトリがない場合"""
        config = Config(github_token="token", github_repo="")
        assert config.is_configured is False

    def test_default_values(self):
        """デフォルト値の確認"""
        config = Config(github_token="t", github_repo="u/r")
        assert config.github_branch == "main"
        assert config.github_api_url == "https://api.github.com"
        assert config.timezone == "Asia/Tokyo"


# ============================================
# DateTimeHelper テスト
# ============================================

class TestDateTimeHelper:
    def setup_method(self):
        self.helper = DateTimeHelper("Asia/Tokyo")

    def test_parse_iso_utc(self):
        """UTC ISO文字列のパース"""
        dt = self.helper.parse_iso("2024-01-15T10:30:00Z")
        assert dt.tzinfo is not None
        # UTC+9 に変換されること
        assert dt.hour == 19  # 10 + 9

    def test_parse_iso_with_offset(self):
        """オフセット付き ISO文字列のパース"""
        dt = self.helper.parse_iso("2024-01-15T10:30:00+09:00")
        assert dt.hour == 10
        assert dt.minute == 30

    def test_parse_iso_invalid_falls_back_to_now(self):
        """無効な文字列はフォールバック"""
        dt = self.helper.parse_iso("invalid-date")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_now(self):
        """現在時刻がタイムゾーン付きで返る"""
        dt = self.helper.now()
        assert dt.tzinfo is not None

    def test_format_date(self):
        """YYYY-MM-DD 形式"""
        tz = ZoneInfo("Asia/Tokyo")
        dt = datetime(2024, 3, 5, 10, 30, tzinfo=tz)
        assert self.helper.format_date(dt) == "2024-03-05"

    def test_format_time(self):
        """HH:MM 形式"""
        tz = ZoneInfo("Asia/Tokyo")
        dt = datetime(2024, 3, 5, 9, 5, tzinfo=tz)
        assert self.helper.format_time(dt) == "09:05"

    def test_format_datetime(self):
        """フルフォーマット"""
        tz = ZoneInfo("Asia/Tokyo")
        dt = datetime(2024, 3, 5, 9, 5, 7, tzinfo=tz)
        assert self.helper.format_datetime(dt) == "2024-03-05 09:05:07"

    def test_format_date_ja(self):
        """日本語日付フォーマット（曜日含む）"""
        tz = ZoneInfo("Asia/Tokyo")
        # 2024-01-01 は月曜日
        dt = datetime(2024, 1, 1, tzinfo=tz)
        result = self.helper.format_date_ja(dt)
        assert "2024年01月01日" in result
        assert "月" in result  # 月曜日

    def test_weekday_ja_all(self):
        """7曜日すべてのフォーマット"""
        tz = ZoneInfo("Asia/Tokyo")
        # 2024-01-01 (月) から 7 日分
        for i, expected in enumerate(WEEKDAY_JA):
            dt = datetime(2024, 1, 1 + i, tzinfo=tz)
            result = self.helper.format_date_ja(dt)
            assert expected in result


# ============================================
# PathGenerator テスト
# ============================================

class TestPathGenerator:
    def test_diary_path(self):
        path = PathGenerator.diary("2024-03-15")
        assert path == "diary/2024/03/15.md"

    def test_transcript_path(self):
        path = PathGenerator.transcript("2024-03-15")
        assert path == "diary/2024/03/15_transcript.md"

    def test_raw_data_path(self):
        path = PathGenerator.raw_data("2024-03-15", "abc123def456")
        assert path == "diary/2024/03/15/raw/abc123def456.json"

    def test_diary_path_single_digit_month_day(self):
        """1桁の月/日のパス"""
        path = PathGenerator.diary("2024-01-05")
        assert path == "diary/2024/01/05.md"


# ============================================
# ContentGenerator テスト
# ============================================

class TestContentGenerator:
    def setup_method(self):
        self.dt_helper = DateTimeHelper("Asia/Tokyo")
        self.generator = ContentGenerator(self.dt_helper)

    def test_diary_header_contains_date(self):
        header = self.generator.diary_header("2024-03-15")
        assert "2024年03月15日" in header
        assert "📔" in header

    def test_diary_header_invalid_date(self):
        """無効な日付でもクラッシュしない"""
        header = self.generator.diary_header("invalid")
        assert "invalid" in header

    def test_diary_entry_basic(self):
        conversation = {
            "id": "conv_abc123",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト会話",
                "overview": "これはテストです。",
                "category": "technology",
                "action_items": []
            },
            "transcript_segments": []
        }
        entry = self.generator.diary_entry(conversation)
        assert "テスト会話" in entry
        assert "これはテストです。" in entry
        assert "💻" in entry  # technology アイコン

    def test_diary_entry_with_action_items(self):
        conversation = {
            "id": "conv_abc123",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "アクションテスト",
                "overview": "概要",
                "category": "other",
                "action_items": [
                    {"description": "タスク1"},
                    {"description": "タスク2"},
                ]
            },
            "transcript_segments": []
        }
        entry = self.generator.diary_entry(conversation)
        assert "タスク1" in entry
        assert "タスク2" in entry
        assert "- [ ]" in entry

    def test_diary_entry_action_items_max_5(self):
        """アクションアイテムは最大5件（タスク0〜タスク4）"""
        action_items = [{"description": f"タスク{i}"} for i in range(10)]
        conversation = {
            "id": "conv_1",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト",
                "overview": "概要",
                "category": "other",
                "action_items": action_items
            },
            "transcript_segments": []
        }
        entry = self.generator.diary_entry(conversation)
        # 5件目(インデックス4)まで含まれ、6件目(インデックス5)以降は含まれない
        assert "タスク4" in entry
        assert "タスク5" not in entry

    def test_diary_entry_with_transcript(self):
        """トランスクリプトがある場合はIDの先頭8文字を含むリンクが生成される"""
        conversation = {
            "id": "conv_abcdefgh",  # 先頭8文字 = "conv_abc"
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト",
                "overview": "概要",
                "category": "other",
                "action_items": []
            },
            "transcript_segments": [{"text": "hello", "speaker": "SPEAKER_00", "start": 0, "end": 5, "is_user": True}]
        }
        entry = self.generator.diary_entry(conversation)
        assert "STT生テキスト" in entry
        assert "conv_abc" in entry  # conversation_id[:8] = "conv_abc"

    def test_diary_entry_unknown_category_fallback_icon(self):
        """未知のカテゴリはデフォルトアイコン"""
        conversation = {
            "id": "conv_1",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト",
                "overview": "概要",
                "category": "unknown_category",
                "action_items": []
            },
            "transcript_segments": []
        }
        entry = self.generator.diary_entry(conversation)
        assert "💬" in entry

    def test_transcript_header(self):
        header = self.generator.transcript_header("2024-03-15")
        assert "2024-03-15" in header
        assert "STT生テキスト" in header

    def test_transcript_entry_with_segments(self):
        conversation = {
            "id": "conv_abc",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {"title": "テスト"},
            "transcript_segments": [
                {"text": "こんにちは", "speaker": "SPEAKER_00", "start": 0, "end": 2, "is_user": True},
                {"text": "元気ですか", "speaker": "SPEAKER_01", "start": 2, "end": 4, "is_user": False},
            ]
        }
        entry = self.generator.transcript_entry(conversation)
        assert "こんにちは" in entry
        assert "元気ですか" in entry
        assert "👤 あなた" in entry
        assert "SPEAKER_01" in entry

    def test_transcript_entry_no_segments(self):
        """セグメントなしの場合"""
        conversation = {
            "id": "conv_abc",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {"title": "テスト"},
            "transcript_segments": []
        }
        entry = self.generator.transcript_entry(conversation)
        assert "STTデータがありません" in entry


# ============================================
# GitHubClient テスト (モック使用)
# ============================================

class TestGitHubClient:
    def setup_method(self):
        self.config = Config(
            github_token="test_token",
            github_repo="user/repo",
            github_branch="main"
        )
        self.client = GitHubClient(self.config)

    def test_headers(self):
        headers = self.client.headers
        assert "Bearer test_token" in headers["Authorization"]
        assert headers["Accept"] == "application/vnd.github+json"

    @pytest.mark.asyncio
    async def test_get_file_success(self):
        """ファイル取得成功"""
        content = "Hello, World!"
        encoded = base64.b64encode(content.encode()).decode()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": encoded, "sha": "abc123"}

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        self.client._client = mock_http_client
        result = await self.client.get_file("diary/2024/03/15.md")

        assert result is not None
        assert result["content"] == content
        assert result["sha"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_file_not_found(self):
        """ファイルが存在しない場合"""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        self.client._client = mock_http_client
        result = await self.client.get_file("diary/2024/03/15.md")
        assert result is None

    @pytest.mark.asyncio
    async def test_put_file(self):
        """ファイル作成/更新"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"commit": {"sha": "new_sha"}}

        mock_http_client = AsyncMock()
        mock_http_client.put = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        self.client._client = mock_http_client
        result = await self.client.put_file("path/file.md", "content", "commit message")
        assert result is not None

    @pytest.mark.asyncio
    async def test_close(self):
        """クライアントのクローズ"""
        mock_http_client = AsyncMock()
        mock_http_client.is_closed = False
        self.client._client = mock_http_client

        await self.client.close()
        mock_http_client.aclose.assert_called_once()


# ============================================
# DiaryService テスト (モック使用)
# ============================================

class TestDiaryService:
    def setup_method(self):
        self.config = Config(
            github_token="test_token",
            github_repo="user/repo",
        )
        self.dt_helper = DateTimeHelper("Asia/Tokyo")
        self.generator = ContentGenerator(self.dt_helper)
        self.github = AsyncMock(spec=GitHubClient)
        self.service = DiaryService(self.github, self.generator)

    @pytest.mark.asyncio
    async def test_save_or_append_creates_new_file(self):
        """ファイルが存在しない場合は新規作成"""
        self.github.get_file = AsyncMock(return_value=None)
        self.github.put_file = AsyncMock()

        status = await self.service.save_or_append(
            path="diary/2024/03/15.md",
            content="新しいエントリ",
            header="# ヘッダー\n",
            commit_message_new="新規作成",
            commit_message_update="更新"
        )
        assert status == "created"
        self.github.put_file.assert_called_once()
        # ヘッダーが先頭に付く
        call_args = self.github.put_file.call_args
        assert "# ヘッダー\n" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_save_or_append_updates_existing_file(self):
        """ファイルが存在する場合は追記"""
        self.github.get_file = AsyncMock(return_value={
            "content": "既存の内容",
            "sha": "existing_sha"
        })
        self.github.put_file = AsyncMock()

        status = await self.service.save_or_append(
            path="diary/2024/03/15.md",
            content="新しいエントリ",
            header="# ヘッダー\n",
            commit_message_new="新規作成",
            commit_message_update="更新"
        )
        assert status == "updated"
        call_args = self.github.put_file.call_args
        # 既存の内容 + 新しいエントリ
        assert "既存の内容" in call_args[0][1]
        assert "新しいエントリ" in call_args[0][1]
        assert call_args[0][3] == "existing_sha"

    @pytest.mark.asyncio
    async def test_save_conversation_full(self):
        """会話を全て保存"""
        self.github.get_file = AsyncMock(return_value=None)
        self.github.put_file = AsyncMock()

        conversation = {
            "id": "conv_test_id_12345678",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト会話",
                "overview": "概要",
                "category": "technology",
                "action_items": []
            },
            "transcript_segments": [
                {"text": "hello", "speaker": "SPEAKER_00", "start": 0, "end": 2, "is_user": True}
            ]
        }

        with patch("main.get_config", return_value=self.config):
            result = await self.service.save_conversation(conversation, "2024-03-15")

        assert result["date"] == "2024-03-15"
        assert result["diary_path"] == "diary/2024/03/15.md"
        assert result["transcript_path"] == "diary/2024/03/15_transcript.md"
        assert result["raw_data_path"] is not None
        assert "conv_test_id_12345678" in result["raw_data_path"]

    @pytest.mark.asyncio
    async def test_save_conversation_no_transcript(self):
        """トランスクリプトなしの会話"""
        self.github.get_file = AsyncMock(return_value=None)
        self.github.put_file = AsyncMock()

        conversation = {
            "id": "conv_no_transcript",
            "created_at": "2024-03-15T10:00:00Z",
            "structured": {
                "title": "テスト",
                "overview": "概要",
                "category": "other",
                "action_items": []
            },
            "transcript_segments": []
        }

        with patch("main.get_config", return_value=self.config):
            result = await self.service.save_conversation(conversation, "2024-03-15")

        assert result["transcript_path"] is None


# ============================================
# API エンドポイント テスト
# ============================================

@pytest.fixture
def client():
    """FastAPI テストクライアント"""
    return TestClient(app)


class TestAPIEndpoints:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "github_configured" in data
        assert data["version"] == "2.0.0"

    def test_root_returns_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Omi GitHub日記" in response.text

    def test_root_shows_unconfigured_status(self, client):
        """未設定の場合はエラー状態を表示"""
        with patch("main.get_config", return_value=Config(github_token="", github_repo="")):
            response = client.get("/")
        assert response.status_code == 200
        assert "未設定" in response.text or "❌" in response.text

    def test_root_shows_configured_status(self, client):
        """設定済みの場合は接続済み状態を表示"""
        with patch("main.get_config", return_value=Config(github_token="t", github_repo="u/r")):
            response = client.get("/")
        assert response.status_code == 200
        assert "接続済み" in response.text or "✅" in response.text

    def test_webhook_requires_config(self, client):
        """設定なしではwebhookが500を返す"""
        with patch("main.get_config", return_value=Config(github_token="", github_repo="")):
            response = client.post("/webhook", json={"id": "test"})
        assert response.status_code == 500

    def test_webhook_invalid_json(self, client):
        """無効なJSONは400を返す（設定あり）"""
        config = Config(github_token="test_token", github_repo="user/repo")
        app.dependency_overrides[require_config] = lambda: config
        try:
            response = client.post(
                "/webhook",
                content="not json",
                headers={"content-type": "application/json"}
            )
        finally:
            app.dependency_overrides.pop(require_config, None)
        assert response.status_code == 400

    def test_webhook_success(self, client):
        """正常なwebhook処理"""
        config = Config(github_token="test_token", github_repo="user/repo")
        mock_service = AsyncMock()
        mock_service.save_conversation = AsyncMock(return_value={
            "date": "2024-03-15",
            "diary_path": "diary/2024/03/15.md",
            "transcript_path": None,
            "raw_data_path": None,
        })

        app.dependency_overrides[require_config] = lambda: config
        try:
            with patch("main.get_diary_service", return_value=mock_service):
                response = client.post("/webhook", json={
                    "id": "conv_test",
                    "created_at": "2024-03-15T10:00:00Z",
                    "structured": {
                        "title": "テスト",
                        "overview": "概要",
                        "category": "other",
                        "action_items": []
                    },
                    "transcript_segments": []
                })
        finally:
            app.dependency_overrides.pop(require_config, None)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "date" in data

    def test_get_diary_not_found(self, client):
        """存在しない日付は404を返す"""
        config = Config(github_token="test_token", github_repo="user/repo")
        mock_github = AsyncMock(spec=GitHubClient)
        mock_github.get_file = AsyncMock(return_value=None)
        mock_github.close = AsyncMock()

        app.dependency_overrides[require_config] = lambda: config
        try:
            with patch("main.GitHubClient", return_value=mock_github):
                response = client.get("/diary/2024-03-15")
        finally:
            app.dependency_overrides.pop(require_config, None)
        assert response.status_code == 404

    def test_get_diary_success(self, client):
        """存在する日付の日記を取得"""
        config = Config(github_token="test_token", github_repo="user/repo")
        mock_github = AsyncMock(spec=GitHubClient)
        mock_github.get_file = AsyncMock(return_value={
            "content": "# 日記の内容",
            "sha": "abc123"
        })
        mock_github.close = AsyncMock()

        app.dependency_overrides[require_config] = lambda: config
        try:
            with patch("main.GitHubClient", return_value=mock_github):
                response = client.get("/diary/2024-03-15")
        finally:
            app.dependency_overrides.pop(require_config, None)
        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2024-03-15"
        assert data["content"] == "# 日記の内容"

    def test_test_endpoint_not_configured(self, client):
        """/test エンドポイント - 未設定の場合"""
        unconfigured = Config(github_token="", github_repo="")
        app.dependency_overrides[get_config] = lambda: unconfigured
        try:
            response = client.get("/test")
        finally:
            app.dependency_overrides.pop(get_config, None)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"

    def test_test_endpoint_configured_success(self, client):
        """/test エンドポイント - 接続成功"""
        config = Config(github_token="token", github_repo="user/repo")
        mock_github = AsyncMock(spec=GitHubClient)
        mock_github.get_repo_info = AsyncMock(return_value={
            "full_name": "user/repo",
            "private": False,
            "html_url": "https://github.com/user/repo"
        })
        mock_github.close = AsyncMock()

        app.dependency_overrides[get_config] = lambda: config
        try:
            with patch("main.GitHubClient", return_value=mock_github):
                response = client.get("/test")
        finally:
            app.dependency_overrides.pop(get_config, None)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["repository"] == "user/repo"


# ============================================
# render_home_page テスト
# ============================================

class TestRenderHomePage:
    def test_unconfigured(self):
        config = Config(github_token="", github_repo="")
        html = render_home_page(config)
        assert "❌" in html
        assert "GitHub未設定" in html

    def test_configured(self):
        config = Config(github_token="token", github_repo="user/repo")
        html = render_home_page(config)
        assert "✅" in html
        assert "接続済み" in html
        assert "https://github.com/user/repo" in html


# ============================================
# 定数テスト
# ============================================

class TestConstants:
    def test_category_icons_coverage(self):
        """主要なカテゴリがアイコン定義に含まれている"""
        required = ["personal", "education", "health", "technology", "other"]
        for cat in required:
            assert cat in CATEGORY_ICONS, f"{cat} が CATEGORY_ICONS に存在しない"

    def test_weekday_ja_length(self):
        """曜日リストが7つある"""
        assert len(WEEKDAY_JA) == 7

    def test_weekday_ja_order(self):
        """曜日の順序: 月火水木金土日"""
        assert WEEKDAY_JA == ["月", "火", "水", "木", "金", "土", "日"]
