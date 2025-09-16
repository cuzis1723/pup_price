import asyncio
import logging
import requests
import os
from datetime import datetime, timedelta, timezone
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# 로깅 설정
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class AdvancedFDVBot:
    def __init__(self, telegram_token):
        """
        고급 FDV + 거래내역 모니터링 봇
        """
        self.bot = Bot(token=telegram_token)
        self.app = Application.builder().token(telegram_token).build()

        # ===== 설정값(필요 시 수정) =====
        self.pool_address = "0xe9c02ca07931f9670fa87217372b3c9aa5a8a934"
        self.network = "hyperevm"
        self.base_url = "https://api.geckoterminal.com/api/v2"
        self.poll_interval_seconds = 60  # 모니터링 주기: 1분
        # 봇 사용을 허용할 텔레그램 사용자 ID 목록 (정수형)
        # 본인/허용 사용자 ID를 여기에 추가하세요.
        self.ALLOWED_USER_IDS = {
            1979227372
        }
        # ============================

        self.previous_fdv = None
        self.previous_trades = {}  # 거래 중복 방지용 캐시
        self.active_chats = set()
        self.monitoring_active = False

        # 마지막 FDV 브로드캐스트 메시지(동일 양식으로 /price에서 사용)
        self.last_fdv_message = None

        # 핸들러 등록
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("price", self.price_command))

    # ───────────────────────────────── commands
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """모니터링 시작(허용 사용자만)"""
        chat_id = update.effective_chat.id
        user = update.effective_user
        user_id = user.id
        user_name = user.first_name or "사용자"

        # 권한 체크
        if self.ALLOWED_USER_IDS and user_id not in self.ALLOWED_USER_IDS:
            await update.message.reply_text(
                "⛔ 이 봇을 시작할 권한이 없습니다.\n관리자에게 허용 사용자 목록에 추가를 요청하세요."
            )
            logger.warning(f"/start 권한 거부: {user_id} ({user_name})")
            return

        self.active_chats.add(chat_id)

        message = f"""
🎉 **{user_name}님, FDV 모니터링 봇이 시작되었습니다!**

📊 **모니터링 내용:**
• 💰 FDV (Fully Diluted Valuation) 변화 추적
• 📈 가격 변화율 표시
• ⏱️ **1분마다** 자동 업데이트

🔧 **사용 가능한 명령어:**
• `/start` - 모니터링 시작
• `/stop` - 모니터링 중단
• `/status` - 현재 상태 확인
• `/price` - 마지막 FDV 업데이트 메세지 보기

🎯 **풀 정보:**
• 주소: `{self.pool_address}`
• 네트워크: {self.network}

이제 1분마다 FDV 업데이트를 받으실 수 있습니다! 🚀
        """.strip()

        await update.message.reply_text(message, parse_mode='Markdown')
        logger.info(f"새로운 사용자 등록: {chat_id} ({user_name})")

        if not self.monitoring_active:
            asyncio.create_task(self.start_monitoring())

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """모니터링 중단"""
        chat_id = update.effective_chat.id

        if chat_id in self.active_chats:
            self.active_chats.remove(chat_id)
            await update.message.reply_text(
                "🛑 **모니터링이 중단되었습니다.**\n\n"
                "다시 시작하려면 `/start` 명령어를 사용하세요.",
                parse_mode='Markdown'
            )
            logger.info(f"사용자 모니터링 중단: {chat_id}")
        else:
            await update.message.reply_text("❌ 현재 모니터링 중이 아닙니다.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재 상태 확인"""
        chat_id = update.effective_chat.id
        is_active = chat_id in self.active_chats

        status_message = f"""
📊 **FDV 모니터링 봇 상태**

🎯 **풀 주소:** `{self.pool_address}`
🌐 **네트워크:** {self.network}
👥 **활성 사용자:** {len(self.active_chats)}명
🔄 **모니터링 상태:** {"✅ 활성" if is_active else "❌ 비활성"}
💰 **현재 FDV:** {self.format_fdv_value(str(self.previous_fdv)) if self.previous_fdv else "아직 없음"}

⏱️ **다음 업데이트:** 1분 이내
        """.strip()

        await update.message.reply_text(status_message, parse_mode='Markdown')

    async def price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """마지막 FDV 업데이트 메시지 출력(모니터링과 동일 양식)"""
        if self.last_fdv_message:
            await update.message.reply_text(self.last_fdv_message, parse_mode='Markdown')
        else:
            await update.message.reply_text("아직 FDV 데이터가 없습니다. 잠시 후 다시 시도해주세요.")

    # ───────────────────────────────── data fetchers
    async def get_pool_data(self):
        """풀 데이터 가져오기"""
        try:
            url = f"{self.base_url}/networks/{self.network}/pools/{self.pool_address}"
            headers = {
                'accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"풀 데이터 API 요청 실패: {e}")
            return None

    async def get_trades_data(self):
        """거래 내역 가져오기"""
        try:
            url = f"{self.base_url}/networks/{self.network}/pools/{self.pool_address}/trades"
            headers = {
                'accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"거래 데이터 API 요청 실패: {e}")
            return None

    # ───────────────────────────────── helpers/formatters
    def format_fdv_value(self, fdv_str):
        """FDV 값 포맷팅"""
        try:
            fdv_float = float(fdv_str)
            if fdv_float >= 1_000_000_000:
                return f"${fdv_float/1_000_000_000:.2f}B"
            elif fdv_float >= 1_000_000:
                return f"${fdv_float/1_000_000:.2f}M"
            elif fdv_float >= 1_000:
                return f"${fdv_float/1_000:.2f}K"
            else:
                return f"${fdv_float:.2f}"
        except:
            return fdv_str

    def calculate_change_percentage(self, current_fdv, previous_fdv):
        """변화율 계산"""
        try:
            if previous_fdv and previous_fdv != 0:
                return ((current_fdv - previous_fdv) / previous_fdv) * 100
            return 0.0
        except:
            return 0.0

    def format_trade_value(self, value_str):
        """거래 금액 포맷팅"""
        try:
            value_float = float(value_str)
            if value_float >= 1_000_000:
                return f"${value_float/1_000_000:.2f}M"
            elif value_float >= 1_000:
                return f"${value_float/1_000:.1f}K"
            else:
                return f"${value_float:.2f}"
        except:
            return value_str

    def get_new_trades(self, trades_data):
        """새로운 거래만 필터링 (최근 1분)"""
        if not trades_data or 'data' not in trades_data:
            return []

        new_trades = []
        current_time = datetime.utcnow()
        one_minute_ago = current_time - timedelta(minutes=1)

        for trade in trades_data['data']:
            trade_id = trade['id']
            attrs = trade['attributes']
            try:
                trade_time = datetime.fromisoformat(
                    attrs['block_timestamp'].replace('Z', '+00:00')
                ).astimezone(timezone.utc).replace(tzinfo=None)
                if trade_time >= one_minute_ago and trade_id not in self.previous_trades:
                    new_trades.append(trade)
                    self.previous_trades[trade_id] = attrs
            except Exception as e:
                logger.error(f"거래 시간 파싱 오류: {e}")
                continue

        # 메모리 관리: 오래된 키 정리
        if len(self.previous_trades) > 1000:
            old_keys = list(self.previous_trades.keys())[:500]
            for k in old_keys:
                del self.previous_trades[k]

        return new_trades

    def format_trades_summary(self, trades_data, limit=10):
        """거래 내역 요약 포맷팅"""
        if not trades_data or 'data' not in trades_data:
            return "❌ 거래 데이터를 찾을 수 없습니다."

        recent_trades = trades_data['data'][:limit]
        message = "📊 **최근 거래 내역 (최신 10개)**\n\n"

        for trade in recent_trades:
            attrs = trade['attributes']
            trade_type = attrs['kind']
            volume_usd = attrs['volume_in_usd']
            timestamp = attrs['block_timestamp']

            emoji = "🟢" if trade_type == "buy" else "🔴"
            action = "매수" if trade_type == "buy" else "매도"

            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M:%S")
            except:
                time_str = timestamp[:8]

            formatted_volume = self.format_trade_value(volume_usd)
            message += f"{emoji} **{action}** {formatted_volume} `{time_str}`\n"

        message += f"\n🔗 [GeckoTerminal에서 더 보기](https://www.geckoterminal.com/{self.network}/pools/{self.pool_address})"
        return message

    def get_kst_time(self):
        """KST(한국 표준시) 시간 반환"""
        kst = timezone(timedelta(hours=9))
        return datetime.now(kst)

    # ───────────────────────────────── broadcasters
    async def broadcast_fdv_update(self, pool_data):
        """FDV 업데이트 브로드캐스트 (+마지막 메시지 저장)"""
        if not self.active_chats or not pool_data:
            return

        try:
            attributes = pool_data['data']['attributes']
            fdv_usd = attributes.get('fdv_usd')
            pool_name = attributes.get('name', '알 수 없는 풀')
            base_token_price = attributes.get('base_token_price_usd', '0')

            if not fdv_usd:
                return

            base_token_price = round(float(base_token_price), 6)
            current_time_kst = self.get_kst_time().strftime("%m-%d %H:%M:%S")
            formatted_fdv = self.format_fdv_value(fdv_usd)
            spot_percentage = str(round(float(fdv_usd) / 1_000_000 / 75 * 100, 1)) + "%"
            current_fdv_float = float(fdv_usd)

            # 변화율
            change_percent = 0.0
            change_emoji = "🔄"
            change_text = ""
            if self.previous_fdv is not None:
                change_percent = self.calculate_change_percentage(current_fdv_float, self.previous_fdv)
                if change_percent > 0:
                    change_emoji = "📈"
                    change_text = f" (+{change_percent:.2f}%)"
                elif change_percent < 0:
                    change_emoji = "📉"
                    change_text = f" ({change_percent:.2f}%)"
                else:
                    change_emoji = "➡️"
                    change_text = " (0.00%)"

            message = f"""
💵 **FDV:** {formatted_fdv}{change_text}
🏁 **SPOT 상장까지:** {spot_percentage}
💰 **FDV 업데이트** {change_emoji}

🎯 **풀:** {pool_name}
📊 **토큰 가격:** ${base_token_price}
🕐 **시간:** {current_time_kst}

🔗 [UPHEAVAL](https://upheaval.fi/portfolio?ref=BASEDONE) | [BASED](https://basedapp.io/r/HLHUB) | [X](https://x.com/pangji_nac) | [TG 공지방](https://t.me/hl_hub_noti)
            """.strip()

            # 마지막 메시지 저장( /price 용 )
            self.last_fdv_message = message

            # 모든 활성 채팅에 전송
            failed_chats = []
            for chat_id in self.active_chats.copy():
                try:
                    await self.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                except TelegramError as e:
                    logger.error(f"FDV 메시지 전송 실패 (Chat {chat_id}): {e}")
                    failed_chats.append(chat_id)

            for chat_id in failed_chats:
                self.active_chats.discard(chat_id)

            self.previous_fdv = current_fdv_float
            logger.info(f"FDV 업데이트 전송 완료: {formatted_fdv}")

        except Exception as e:
            logger.error(f"FDV 브로드캐스트 중 오류: {e}")

    async def broadcast_new_trades(self, new_trades):
        """새로운 거래 알림 브로드캐스트"""
        if not self.active_chats or not new_trades:
            return

        try:
            sorted_trades = sorted(
                new_trades,
                key=lambda x: float(x['attributes']['volume_in_usd']),
                reverse=True
            )
            top_trades = sorted_trades[:5]

            for trade in top_trades:
                attrs = trade['attributes']
                trade_type = attrs['kind']
                volume_usd = attrs['volume_in_usd']
                timestamp = attrs['block_timestamp']

                if float(volume_usd) < 100:
                    continue

                if trade_type == "buy":
                    emoji = "🟢"; action = "대량 매수"; color = "📈"
                else:
                    emoji = "🔴"; action = "대량 매도"; color = "📉"

                formatted_volume = self.format_trade_value(volume_usd)
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    time_str = dt.strftime("%H:%M:%S")
                except:
                    time_str = "방금"

                message = f"""
{emoji} **{action} 알림** {color}

💰 **거래량:** {formatted_volume}
🕐 **시간:** {time_str}
🔗 **확인:** [GeckoTerminal](https://www.geckoterminal.com/{self.network}/pools/{self.pool_address})
                """.strip()

                failed_chats = []
                for chat_id in self.active_chats.copy():
                    try:
                        await self.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                    except TelegramError as e:
                        logger.error(f"거래 알림 전송 실패 (Chat {chat_id}): {e}")
                        failed_chats.append(chat_id)

                for chat_id in failed_chats:
                    self.active_chats.discard(chat_id)

                logger.info(f"거래 알림 전송: {action} {formatted_volume}")

        except Exception as e:
            logger.error(f"거래 알림 브로드캐스트 중 오류: {e}")

    # ───────────────────────────────── loop
    async def start_monitoring(self):
        """모니터링 시작"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        logger.info("고급 FDV + 거래 모니터링 시작...")

        while self.monitoring_active and self.active_chats:
            try:
                pool_data = await self.get_pool_data()
                if pool_data:
                    await self.broadcast_fdv_update(pool_data)

                trades_data = await self.get_trades_data()
                if trades_data:
                    new_trades = self.get_new_trades(trades_data)
                    if new_trades:
                        await self.broadcast_new_trades(new_trades)

                await asyncio.sleep(self.poll_interval_seconds)

            except Exception as e:
                logger.error(f"모니터링 중 오류: {e}")
                await asyncio.sleep(self.poll_interval_seconds)

        self.monitoring_active = False
        logger.info("모니터링 중단됨")

    # ───────────────────────────────── runner
    def run(self):
        """봇 실행"""
        logger.info("고급 FDV + 거래 모니터링 봇 시작...")
        self.app.run_polling()


def main():
    """메인 함수"""
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_BOT_TOKEN:
        print("❌ 오류: TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다!")
        print("Railway Variables 탭에서 텔레그램 봇 토큰을 설정하세요.")
        return

    bot = AdvancedFDVBot(TELEGRAM_BOT_TOKEN)

    # 필요 시 여기서 허용 사용자 ID를 코드로 주입할 수도 있습니다.
    # 예: bot.ALLOWED_USER_IDS.update({123456789, 987654321})

    bot.run()

if __name__ == "__main__":
    main()
