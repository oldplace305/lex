"""バックアップCog - 設定・会話・事業データの自動バックアップ。
毎日深夜3:00（JST）に data/ ディレクトリ全体をバックアップする。
手動バックアップ・復元コマンドも提供。
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot.utils.paths import DATA_DIR, PROJECT_ROOT
from bot.config import OWNER_ID

logger = logging.getLogger(__name__)

# 日本時間
JST = timezone(timedelta(hours=9))

# バックアップ先
BACKUP_DIR = PROJECT_ROOT / "backups"

# バックアップ保持数（これを超えると古いものから削除）
MAX_BACKUPS = 14  # 2週間分

# 自動バックアップ時刻（JST）
BACKUP_HOUR = 3  # 午前3時


class Backup(commands.Cog):
    """設定・データの自動バックアップを行うCog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Cog読み込み時にバックアップスケジューラーを開始。"""
        self.auto_backup_loop.start()
        logger.info("⚡ 自動バックアップスケジューラー開始")

    async def cog_unload(self):
        """Cog解除時にスケジューラーを停止。"""
        self.auto_backup_loop.cancel()
        logger.info("⚡ 自動バックアップスケジューラー停止")

    def _create_backup(self) -> str:
        """バックアップを作成する。

        Returns:
            str: バックアップディレクトリ名
        """
        now = datetime.now(JST)
        backup_name = now.strftime("backup_%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / backup_name

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # data/ ディレクトリ全体をコピー
        shutil.copytree(DATA_DIR, backup_path / "data")

        # .env もバックアップ（トークン含むため重要）
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            shutil.copy2(env_file, backup_path / ".env")

        logger.info(f"バックアップ作成完了: {backup_name}")
        return backup_name

    def _cleanup_old_backups(self):
        """古いバックアップを削除。"""
        if not BACKUP_DIR.exists():
            return

        backups = sorted(
            [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )

        # MAX_BACKUPSを超えた分を削除
        for old_backup in backups[MAX_BACKUPS:]:
            shutil.rmtree(old_backup)
            logger.info(f"古いバックアップ削除: {old_backup.name}")

    def _list_backups(self) -> list:
        """バックアップ一覧を取得。"""
        if not BACKUP_DIR.exists():
            return []

        backups = sorted(
            [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )

        result = []
        for b in backups:
            # サイズ計算
            size_bytes = sum(
                f.stat().st_size for f in b.rglob("*") if f.is_file()
            )
            size_kb = round(size_bytes / 1024, 1)
            result.append({
                "name": b.name,
                "size_kb": size_kb,
                "path": str(b),
            })

        return result

    @tasks.loop(minutes=1)
    async def auto_backup_loop(self):
        """毎分チェックし、バックアップ時刻に自動バックアップ。"""
        now = datetime.now(JST)

        if now.hour != BACKUP_HOUR or now.minute != 0:
            return

        try:
            backup_name = self._create_backup()
            self._cleanup_old_backups()
            logger.info(f"⚡ 自動バックアップ完了: {backup_name}")
        except Exception as e:
            logger.error(f"自動バックアップ失敗: {e}", exc_info=True)

    @auto_backup_loop.before_loop
    async def before_auto_backup(self):
        """Bot起動完了を待つ。"""
        await self.bot.wait_until_ready()

    # --- スラッシュコマンド ---

    @app_commands.command(name="backup", description="今すぐバックアップを作成")
    async def backup_now(self, interaction: discord.Interaction):
        """手動バックアップ。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            backup_name = self._create_backup()
            self._cleanup_old_backups()

            backups = self._list_backups()
            latest = backups[0] if backups else None

            embed = discord.Embed(
                title="⚡ バックアップ完了",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="作成",
                value=backup_name,
                inline=False,
            )
            if latest:
                embed.add_field(
                    name="サイズ",
                    value=f"{latest['size_kb']} KB",
                    inline=True,
                )
            embed.add_field(
                name="保持数",
                value=f"{len(backups)} / {MAX_BACKUPS}",
                inline=True,
            )
            embed.set_footer(
                text=f"バックアップ先: {BACKUP_DIR}"
            )
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"手動バックアップ失敗: {e}", exc_info=True)
            await interaction.followup.send(
                f"⚠️ バックアップ失敗: {e}"
            )

    @app_commands.command(name="backup_list", description="バックアップ一覧を表示")
    async def backup_list(self, interaction: discord.Interaction):
        """バックアップ一覧。"""
        if not interaction.user.id == OWNER_ID:
            await interaction.response.send_message(
                "⚡ このコマンドはオーナー専用です。", ephemeral=True
            )
            return

        backups = self._list_backups()

        if not backups:
            await interaction.response.send_message(
                "⚡ バックアップはまだありません。`/backup` で作成できます。"
            )
            return

        embed = discord.Embed(
            title=f"⚡ バックアップ一覧（{len(backups)}件）",
            color=discord.Color.blue(),
        )

        for i, b in enumerate(backups[:10]):  # 最大10件表示
            embed.add_field(
                name=f"{i+1}. {b['name']}",
                value=f"サイズ: {b['size_kb']} KB",
                inline=False,
            )

        embed.set_footer(
            text=f"自動バックアップ: 毎日 {BACKUP_HOUR}:00 JST | 保持: 最大{MAX_BACKUPS}世代"
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Cogを登録。"""
    await bot.add_cog(Backup(bot))
