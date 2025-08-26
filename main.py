import asyncio
import logging
import requests
import os
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
import json

# 로깅 설정
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class GeckoTerminalFDVBot:
    def __init__(self, telegram_token, chat_id):
        """
        텔레그램 봇 초기화
        
        Args:
            telegram_token (str): 텔레그램 봇 토큰
            chat_id (str): 메시지를 보낼 채팅 ID
        """
        self.bot = Bot(token=telegram_token)
        self.chat_id = chat_id
        self.pool_address = "0xe9c02ca07931f9670fa87217372b3c9aa5a8a934"
        self.network = "hyperevm"
        self.base_url = "https://api.geckoterminal.com/api/v2"
        self.previous_fdv = None
        
    async def get_pool_data(self):
        """
        GeckoTerminal API에서 풀 데이터 가져오기
        
        Returns:
            dict: 풀 데이터 또는 None (실패시)
        """
        try:
            url = f"{self.base_url}/networks/{self.network}/pools/{self.pool_address}"
            
            # API 요청 헤더
            headers = {
                'accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API 요청 실패: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}")
            return None
        except Exception as e:
            logger.error(f"예상치 못한 오류: {e}")
            return None
    
    def format_fdv_value(self, fdv_str):
        """
        FDV 값을 읽기 좋은 형태로 포맷팅
        
        Args:
            fdv_str (str): FDV 값 문자열
            
        Returns:
            str: 포맷팅된 FDV 값
        """
        try:
            fdv_float = float(fdv_str)
            
            if fdv_float >= 1_000_000_000:  # 10억 이상
                return f"${fdv_float/1_000_000_000:.2f}B"
            elif fdv_float >= 1_000_000:    # 100만 이상
                return f"${fdv_float/1_000_000:.2f}M"
            elif fdv_float >= 1_000:        # 1천 이상
                return f"${fdv_float/1_000:.2f}K"
            else:
                return f"${fdv_float:.2f}"
                
        except (ValueError, TypeError):
            return fdv_str
    
    def calculate_change_percentage(self, current_fdv, previous_fdv):
        """
        FDV 변화율 계산
        
        Args:
            current_fdv (float): 현재 FDV
            previous_fdv (float): 이전 FDV
            
        Returns:
            float: 변화율 (%)
        """
        try:
            if previous_fdv and previous_fdv != 0:
                change = ((current_fdv - previous_fdv) / previous_fdv) * 100
                return change
            return 0.0
        except (TypeError, ZeroDivisionError):
            return 0.0
    
    async def send_fdv_update(self, pool_data):
        """
        FDV 업데이트를 텔레그램으로 전송
        
        Args:
            pool_data (dict): 풀 데이터
        """
        try:
            if not pool_data or 'data' not in pool_data:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text="⚠️ 풀 데이터를 가져올 수 없습니다."
                )
                return
            
            attributes = pool_data['data']['attributes']
            
            # FDV 값 추출
            fdv_usd = attributes.get('fdv_usd')
            pool_name = attributes.get('name', '알 수 없는 풀')
            base_token_price = attributes.get('base_token_price_usd', '0')
            quote_token_price = attributes.get('quote_token_price_usd', '0')
            
            if not fdv_usd:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text="⚠️ FDV 데이터를 찾을 수 없습니다."
                )
                return
            
            # 현재 시간
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # FDV 포맷팅
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
            
            # 메시지 구성
            message = f"""
🏊‍♂️ <b>풀 FDV 업데이트</b>

🎯 <b>풀 이름:</b> {pool_name}
🌐 <b>네트워크:</b> HyperEVM
💰 <b>FDV:</b> {formatted_fdv}{change_text} {change_emoji}

📊 <b>토큰 가격:</b>
• Base Token: ${base_token_price}
• Quote Token: ${quote_token_price}

🕐 <b>업데이트 시간:</b> {current_time}
🔗 <b>풀 주소:</b> <code>{self.pool_address}</code>
            """.strip()
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML'
            )
            
            # 이전 FDV 값 업데이트
            self.previous_fdv = current_fdv_float
            
            logger.info(f"FDV 업데이트 전송 완료: {formatted_fdv}")
            
        except TelegramError as e:
            logger.error(f"텔레그램 메시지 전송 실패: {e}")
        except Exception as e:
            logger.error(f"FDV 업데이트 전송 중 오류: {e}")
    
    async def start_monitoring(self):
        """
        FDV 모니터링 시작 (1분마다 실행)
        """
        logger.info("FDV 모니터링 시작...")
        
        # 시작 메시지 전송
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"🤖 <b>FDV 모니터링 봇이 시작되었습니다!</b>\n\n"
                     f"🎯 풀 주소: <code>{self.pool_address}</code>\n"
                     f"🌐 네트워크: {self.network}\n"
                     f"⏱️ 업데이트 주기: 1분마다",
                parse_mode='HTML'
            )
        except TelegramError as e:
            logger.error(f"시작 메시지 전송 실패: {e}")
        
        while True:
            try:
                logger.info("풀 데이터 조회 중...")
                pool_data = await self.get_pool_data()
                
                if pool_data:
                    await self.send_fdv_update(pool_data)
                else:
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text="⚠️ 풀 데이터 조회 실패 - 다음 주기에 재시도합니다."
                    )
                
                # 1분 대기
                logger.info("60초 대기 중...")
                await asyncio.sleep(60)
                
            except KeyboardInterrupt:
                logger.info("사용자에 의해 모니터링이 중단되었습니다.")
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text="🛑 <b>FDV 모니터링이 중단되었습니다.</b>",
                    parse_mode='HTML'
                )
                break
            except Exception as e:
                logger.error(f"모니터링 중 오류 발생: {e}")
                try:
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=f"⚠️ 모니터링 중 오류 발생: {str(e)}\n재시도 중..."
                    )
                except:
                    pass
                await asyncio.sleep(60)

async def main():
    """
    메인 함수 - 봇 설정 및 실행
    """
    # Railway 환경변수에서 설정 값 불러오기
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    
    # 환경변수가 설정되지 않은 경우 오류 메시지
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("❌ 오류: 환경변수가 설정되지 않았습니다!")
        print("\n📝 Railway에서 설정해야 할 환경변수:")
        print("1. TELEGRAM_BOT_TOKEN: BotFather에서 생성한 봇 토큰")
        print("2. CHAT_ID: 메시지를 받을 채팅 ID")
        print("\n🔧 Railway 설정 방법:")
        print("1. Railway 프로젝트 대시보드 접속")
        print("2. Variables 탭 클릭")
        print("3. 위 환경변수들 추가")
        return
    
    # 봇 인스턴스 생성
    bot = GeckoTerminalFDVBot(
        telegram_token=TELEGRAM_BOT_TOKEN,
        chat_id=CHAT_ID
    )
    
    # 모니터링 시작
    await bot.start_monitoring()

if __name__ == "__main__":
    # Railway 환경에서는 자동으로 패키지가 설치되므로 import 체크 생략
    print("🚀 FDV 텔레그램 봇을 시작합니다...")
    print("🌐 Railway 환경에서 실행 중...")
    
    # 봇 실행
    asyncio.run(main())
