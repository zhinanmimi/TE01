from dotenv import load_dotenv # type: ignore
import os
import pytz # type: ignore

load_dotenv()

# Telegram配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# 数据库配置
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://render:qBtmtGOWMAjmrzfJ0fk0Mmt7VlE6Xyto@dpg-ct9cma1u0jms73cpbsug-a.oregon-postgres.render.com/botdb_psdk')

# 消息配置
WARNING_MESSAGE = "⚠️ 警告：检测到违规内容，消息已被删除。" 