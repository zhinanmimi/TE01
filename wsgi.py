from main import web_app
import asyncio

# 创建事件循环
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# 获取应用实例
app = loop.run_until_complete(web_app()) 