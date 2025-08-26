import asyncio
import logging
import requests
import os
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler
from telegram.error import TelegramError
import json

# 로깅 설정
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class SmartFDVBot:
    def __init__(self, telegram_token):
        """
        스마트 FDV 봇 - chat_id 자동 감지
        """
        self.bot = Bot(token=telegram_token)
        self.app = Application.builder().token(telegram_token).build()
        self.pool_address = "0xe9c02ca07931f9670fa87217372b3c9aa5a8a934"
        self.network = "hyperevm"
        self.base_url = "https://api.geckoterminal.com/api/v2"
        self.previous_fdv = None
        self.active_chats = set()  # 활성 채팅 ID들 저장
        self.monitoring_active = False
        
        # 핸들러 추가
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        
    async def start_command(self, update: Update, context):
        """사용자가 /start 명령어를 보냈을 때"""
        chat_id = update.effective_chat.id
        user_name = update.effective_user.first_name or "사용자"
        
        # 활성 채팅 목록에 추가
        self.active_chats.add(chat_id)
        
        message = f"""
🎉 안녕하세요 {user_name}님!

🤖 **FDV 모니터링 봇이 활성화되었습니다!**

📊 **모니터링 정보:**
🎯 풀: {self.pool_address}
🌐 네트워크: {self.network}
⏱️ 업데이트: 1분마다

🔧 **사용 가능한 명령어:**
• `/start` - 모니터링 시작
• `/stop` - 모니터링 중단  
• `/status` - 현재 상태 확인

💡 이제 1분마다 FDV 업데이트를 받으실 수 있습니다!
        """
        
        await update.message.reply_text(message)
        logger.info(f"새로운 사용자 등록: {chat_id} ({user_name})")
        
        # 모니터링이 아직 시작되지 않았다면 시작
        if not self.monitoring_active:
            asyncio.create_task(self.start_monitoring())
    
    async def stop_command(self, update: Update, context):
        """사용자가 /stop 명령어를 보냈을 때"""
        chat_id = update.effective_chat.id
        
        if chat_id in self.active_chats:
            self.active_chats.remove(chat_id)
            await update.message.reply_text(
                "🛑 **모니터링이 중단되었습니다.**\n\n"
                "다시 시작하려면 `/start` 명령어를 사용하세요."
            )
            logger.info(f"사용자 모니터링 중단: {chat_id}")
        else:
            await update.message.reply_text("❌ 현재 모니터링 중이 아닙니다.")
    
    async def status_command(self, update: Update, context):
        """현재 상태 확인"""
        chat_id = update.effective_chat.id
        is_active = chat_id in self.active_chats
        
        status_message = f"""
📊 **FDV 봇 상태**

🎯 **풀 주소:** `{self.pool_address}`
🌐 **네트워크:** {self.network}
👥 **활성 사용자:** {len(self.active_chats)}명
🔄 **모니터링 상태:** {"✅ 활성" if is_active else "❌ 비활성"}
💰 **마지막 FDV:** {self.format_fdv_value(str(self.previous_fdv)) if self.previous_fdv else "아직 없음"}

⏱️ **다음 업데이트:** 1분 이내
        """
        
        await update.message.reply_text(status_message, parse_mode='Markdown')
    
    async def get_pool_data(self):
        """GeckoTerminal API에서 풀 데이터 가져오기"""
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
            logger.error(f"API 요청 실패: {e}")
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
    
    def calculate_change_percentage(self, current_fdv, previous_fdv):
        """변화율 계산"""
        try:
            if previous_fdv and previous_fdv != 0:
                return ((current_fdv - previous_fdv) / previous_fdv) * 100
            return 0.0
        except:
            return 0.0
    
    async def broadcast_update(self, pool_data):
        """모든 활성 채팅에 업데이트 전송"""
        if not self.active_chats:
            return
        
        try:
            attributes = pool_data['data']['attributes']
            fdv_usd = attributes.get('fdv_usd')
            pool_name = attributes.get('name', '알 수 없는 풀')
            base_token_price = attributes.get('base_token_price_usd', '0')
            
            if not fdv_usd:
                return
            
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
🏊‍♂️ **풀 FDV 업데이트**

🎯 **풀:** {pool_name}
🌐 **네트워크:** HyperEVM
💰 **FDV:** {formatted_fdv}{change_text} {change_emoji}

📊 **Base Token 가격:** ${base_token_price}

🕐 **업데이트:** {current_time}
            """.strip()
            
            # 모든 활성 채팅에 메시지 전송
            failed_chats = []
            for chat_id in self.active_chats.copy():
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                except TelegramError as e:
                    logger.error(f"메시지 전송 실패 (Chat {chat_id}): {e}")
                    failed_chats.append(chat_id)
            
            # 실패한 채팅 제거
            for chat_id in failed_chats:
                self.active_chats.discard(chat_id)
            
            self.previous_fdv = current_fdv_float
            logger.info(f"업데이트 전송 완료: {formatted_fdv} to {len(self.active_chats)} chats")
            
        except Exception as e:
            logger.error(f"브로드캐스트 중 오류: {e}")
    
    async def start_monitoring(self):
        """FDV 모니터링 시작"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        logger.info("FDV 모니터링 시작...")
        
        while self.monitoring_active and self.active_chats:
            try:
                pool_data = await self.get_pool_data()
                if pool_data:
                    await self.broadcast_update(pool_data)
                
                await asyncio.sleep(60)  # 1분 대기
                
            except Exception as e:
                logger.error(f"모니터링 중 오류: {e}")
                await asyncio.sleep(60)
        
        self.monitoring_active = False
        logger.info("FDV 모니터링 중단됨")
    
    def run(self):
        """봇 실행"""
        logger.info("스마트 FDV 봇 시작...")
        self.app.run_polling()

def main():
    """메인 함수"""
    # Railway 환경변수에서 토큰 가져오기
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 오류: TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다!")
        print("Railway Variables 탭에서 텔레그램 봇 토큰을 설정하세요.")
        return
    
    # 봇 실행
    bot = SmartFDVBot(TELEGRAM_BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
