import asyncio
import logging
import requests
import os
from datetime import datetime, timedelta, timezone
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError
import json

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
        self.pool_address = "0xe9c02ca07931f9670fa87217372b3c9aa5a8a934"
        self.network = "hyperevm"
        self.base_url = "https://api.geckoterminal.com/api/v2"
        self.previous_fdv = None

        self.active_chats = set()
        self.monitoring_active = False
        
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """모니터링 시작"""
        chat_id = update.effective_chat.id
        user_name = update.effective_user.first_name or "사용자"
        
        self.active_chats.add(chat_id)
        
        message = f"""
🎉 **{user_name}님, FDV 모니터링 봇이 시작되었습니다!**

📊 **모니터링 내용:**
• 💰 FDV (Fully Diluted Valuation) 변화 추적
• 📈 가격 변화율 표시
• ⏱️ 5분마다 자동 업데이트

🔧 **사용 가능한 명령어:**
• `/start` - 모니터링 시작
• `/stop` - 모니터링 중단
• `/status` - 현재 상태 확인

🎯 **풀 정보:**
• 주소: `{self.pool_address}`
• 네트워크: {self.network}

이제 5분마다 FDV 업데이트를 받으실 수 있습니다! 🚀
        """
        
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

⏱️ **다음 업데이트:** 5분 이내
        """
        
        await update.message.reply_text(status_message, parse_mode='Markdown')
    
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
    
    def get_kst_time(self):
        """KST(한국 표준시) 시간 반환"""
        kst = timezone(timedelta(hours=9))
        return datetime.now(kst)
        """변화율 계산"""
        try:
            if previous_fdv and previous_fdv != 0:
                return ((current_fdv - previous_fdv) / previous_fdv) * 100
            return 0.0
        except:
            return 0.0
    
    async def broadcast_fdv_update(self, pool_data):
        """FDV 업데이트 브로드캐스트"""
        if not self.active_chats or not pool_data:
            return
        
        try:
            attributes = pool_data['data']['attributes']
            fdv_usd = attributes.get('fdv_usd')
            pool_name = attributes.get('name', '알 수 없는 풀')
            base_token_price = attributes.get('base_token_price_usd', '0')
            
            if not fdv_usd:
                return
            
            current_time_kst = self.get_kst_time().strftime("%m-%d %H:%M:%S")
            formatted_fdv = self.format_fdv_value(fdv_usd)
            current_fdv_float = float(fdv_usd)
            
            # 변화율 계산
            change_percent = 0.0
            change_emoji = "🔄"
            change_text = ""
            
            if self.previous_fdv is not None:
                change_percent = self.calculate_change_percentage(
                    current_fdv_float, self.previous_fdv
                )
                
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
📊 **토큰 가격:** ${base_token_price}

{formatted_fdv} **FDV 업데이트** {change_emoji}
🎯 **풀:** {pool_name}
🕐 **시간:** {current_time_kst} (KST)

🔗 [차트보기](https://upheaval.fi/swap)
            """.strip()
            
            # 모든 활성 채팅에 전송
            failed_chats = []
            for chat_id in self.active_chats.copy():
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                except TelegramError as e:
                    logger.error(f"FDV 메시지 전송 실패 (Chat {chat_id}): {e}")
                    failed_chats.append(chat_id)
            
            # 실패한 채팅 제거
            for chat_id in failed_chats:
                self.active_chats.discard(chat_id)
            
            self.previous_fdv = current_fdv_float
            logger.info(f"FDV 업데이트 전송 완료: {formatted_fdv}")
            
        except Exception as e:
            logger.error(f"FDV 브로드캐스트 중 오류: {e}")
    
    async def start_monitoring(self):
        """모니터링 시작"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        logger.info("FDV 모니터링 시작...")
        
        while self.monitoring_active and self.active_chats:
            try:
                # FDV 데이터 가져오기
                pool_data = await self.get_pool_data()
                if pool_data:
                    await self.broadcast_fdv_update(pool_data)
                
                # 5분 대기
                await asyncio.sleep(300)
                
            except Exception as e:
                logger.error(f"모니터링 중 오류: {e}")
                await asyncio.sleep(300)
        
        self.monitoring_active = False
        logger.info("모니터링 중단됨")
    
    def run(self):
        """봇 실행"""
        logger.info("FDV 모니터링 봇 시작...")
        self.app.run_polling()

def main():
    """메인 함수"""
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 오류: TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다!")
        print("Railway Variables 탭에서 텔레그램 봇 토큰을 설정하세요.")
        return
    
    # 봇 실행
    bot = AdvancedFDVBot(TELEGRAM_BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
