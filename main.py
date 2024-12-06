import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # type: ignore
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler # type: ignore
from config import BOT_TOKEN, BEIJING_TZ, WARNING_MESSAGE
from models import Session, User, Group, ScheduledMessage
import os
from aiohttp import web # type: ignore

# 设置日志
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# 添加状态常量
(
    COLLECTING_MESSAGES,
    SELECTING_GROUP,
    SELECTING_TIME,
    CONFIRMING_DELETE,
    SELECTING_DELETE_TIME,
) = range(5)

# 修改消息缓存结构
message_cache = {}

class MessageItem:
    def __init__(self, content, type='text'):
        self.content = content
        self.type = type  # 'text', 'photo', 'video'
        self.file_id = None  # 用于存储媒体文件的file_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return
        
    keyboard = [
        [InlineKeyboardButton("📅 定时发送消息", callback_data='schedule_message')],
        [InlineKeyboardButton("👥 群组管理", callback_data='manage_groups')],
        [InlineKeyboardButton("⚙️ 白名单管理", callback_data='manage_whitelist')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "欢迎使用管理机器人！\n请选择以下功能：",
        reply_markup=reply_markup
    )

async def check_user_permission(user_id: int, permission: str) -> bool:
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    session.close()
    
    if not user:
        return False
    
    return getattr(user, permission, False)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        return
        
    session = Session()
    try:
        group = session.query(Group).filter_by(group_id=update.effective_chat.id).first()
        if not group or not group.monitoring_enabled:
            return
            
        message = update.message
        should_delete = False
        reason = ""
        
        # 检查是否是机器人消息
        if message.from_user.is_bot and not is_group_bot(update.effective_chat.id, message.from_user.id):
            should_delete = True
            reason = "非本群机器人消息"
            
        # 检查链接
        elif message.entities:
            for entity in message.entities:
                if entity.type in ['url', 'text_link']:
                    should_delete = True
                    reason = "包含链接"
                    break
        
        # 允许转发本群消息
        elif message.forward_from_chat:
            if message.forward_from_chat.id != update.effective_chat.id:
                should_delete = True
                reason = "非本群转发消息"
        
        if should_delete:
            await message.delete()
            warning = f"⚠️ 警：检测到{reason}，消息已被删除。"
            await update.effective_chat.send_message(warning)
            
    finally:
        session.close()

def is_group_bot(group_id: int, bot_id: int) -> bool:
    # 这里可以添加检查机器人是否属于该群的逻辑
    # 可以维护一个群组机器人的列表
    return False

async def schedule_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await check_user_permission(user_id, 'can_schedule'):
        await query.answer("您没有权限使用此功能！")
        return ConversationHandler.END
    
    message_cache[user_id] = {
        'messages': [],
        'selected_group': None,
        'schedule_time': None,
        'delete_time': None
    }
    
    await query.answer()
    await query.message.reply_text(
        "请发送要定时发送的消息，支持：\n"
        "- 文本消息\n"
        "- 图片\n"
        "- 视频\n"
        "发送完成后请输入 END"
    )
    return COLLECTING_MESSAGES

async def collect_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message
    
    if message.text == "END":
        if not message_cache[user_id]['messages']:
            await message.reply_text("您还没有输入任何消息！请重新开始。")
            return ConversationHandler.END
            
        # 显示已收集的消息预览
        preview = "已收集的消息：\n\n"
        for idx, msg in enumerate(message_cache[user_id]['messages'], 1):
            if msg.type == 'text':
                preview += f"{idx}. 文本: {msg.content[:30]}...\n"
            elif msg.type == 'photo':
                preview += f"{idx}. 图片\n"
            elif msg.type == 'video':
                preview += f"{idx}. 视频\n"
        
        # 获取可用群组列表
        session = Session()
        groups = session.query(Group).all()
        session.close()
        
        keyboard = [[InlineKeyboardButton(group.group_name, callback_data=f"group_{group.group_id}")] 
                   for group in groups]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.reply_text(preview)
        await message.reply_text("请选择要发送到的群组：", reply_markup=reply_markup)
        return SELECTING_GROUP
    
    # 处理不同类型的消息
    if message.text:
        msg_item = MessageItem(message.text, 'text')
    elif message.photo:
        # 获取最高质量的图片
        photo = message.photo[-1]
        msg_item = MessageItem(photo.file_id, 'photo')
    elif message.video:
        msg_item = MessageItem(message.video.file_id, 'video')
    else:
        await message.reply_text("不支持的消息类型！请发送文本、图片或视频。")
        return COLLECTING_MESSAGES
    
    message_cache[user_id]['messages'].append(msg_item)
    await message.reply_text(f"消息已记录，继续发送下一条或输入 END 结束")
    return COLLECTING_MESSAGES

async def select_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    group_id = int(query.data.split('_')[1])
    
    message_cache[user_id]['selected_group'] = group_id
    
    keyboard = [
        [InlineKeyboardButton("30秒后", callback_data="time_30s")],
        [InlineKeyboardButton("5分钟后", callback_data="time_5m")],
        [InlineKeyboardButton("每天间隔一小时", callback_data="time_daily")],
        [InlineKeyboardButton("自定义时间", callback_data="time_custom")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("请选择发送时间：", reply_markup=reply_markup)
    return SELECTING_TIME

async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    time_choice = query.data.split('_')[1]
    
    now = datetime.now(BEIJING_TZ)
    
    if time_choice == "30s":
        schedule_time = now + timedelta(seconds=30)
    elif time_choice == "5m":
        schedule_time = now + timedelta(minutes=5)
    elif time_choice == "daily":
        schedule_time = now + timedelta(hours=1)
    else:
        # 理自定义时间的情况
        await query.edit_message_text("请输入具体时间（格式：YYYY-MM-DD HH:MM）：")
        return SELECTING_TIME
    
    message_cache[user_id]['schedule_time'] = schedule_time
    
    keyboard = [
        [InlineKeyboardButton("是", callback_data="delete_yes")],
        [InlineKeyboardButton("否", callback_data="delete_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("是否需要自动删除消息？", reply_markup=reply_markup)
    return CONFIRMING_DELETE

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    choice = query.data.split('_')[1]
    
    if choice == "no":
        # 直接保存定时消息
        await save_scheduled_message(user_id)
        await query.edit_message_text("定时消息已设置成功！")
        return ConversationHandler.END
    
    # 如果选择删除，显示删除时间选项
    keyboard = [
        [InlineKeyboardButton("30秒后", callback_data="deltime_30s")],
        [InlineKeyboardButton("5分钟后", callback_data="deltime_5m")],
        [InlineKeyboardButton("1小时后", callback_data="deltime_1h")],
        [InlineKeyboardButton("自定义时间", callback_data="deltime_custom")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("请选择删除时间", reply_markup=reply_markup)
    return SELECTING_DELETE_TIME

async def select_delete_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    time_choice = query.data.split('_')[1]
    
    schedule_time = message_cache[user_id]['schedule_time']
    
    if time_choice == "30s":
        delete_time = schedule_time + timedelta(seconds=30)
    elif time_choice == "5m":
        delete_time = schedule_time + timedelta(minutes=5)
    elif time_choice == "1h":
        delete_time = schedule_time + timedelta(hours=1)
    elif time_choice == "custom":
        await query.edit_message_text("请输入删除时间（格式：YYYY-MM-DD HH:MM）：")
        return SELECTING_DELETE_TIME
    
    message_cache[user_id]['delete_time'] = delete_time
    
    # 保存定时消息
    await save_scheduled_message(user_id)
    await query.edit_message_text("定时消息已设置成功！")
    return ConversationHandler.END

async def save_scheduled_message(user_id: int):
    """保存定时消息到数据库"""
    cache = message_cache[user_id]
    session = Session()
    
    for msg_item in cache['messages']:
        scheduled_msg = ScheduledMessage(
            user_id=user_id,
            group_id=cache['selected_group'],
            message_text=msg_item.content,
            message_type=msg_item.type,
            schedule_time=cache['schedule_time'],
            delete_time=cache.get('delete_time')
        )
        session.add(scheduled_msg)
    
    try:
        session.commit()
        # 生成预览信息
        preview = "定时消息设置成功！\n\n"
        preview += f"发送时间：{cache['schedule_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        if cache.get('delete_time'):
            preview += f"删除时间：{cache['delete_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        preview += "\n消息列表：\n"
        for idx, msg in enumerate(cache['messages'], 1):
            if msg.type == 'text':
                preview += f"{idx}. 文本: {msg.content[:30]}...\n"
            elif msg.type == 'photo':
                preview += f"{idx}. 图片\n"
            elif msg.type == 'video':
                preview += f"{idx}. 视频\n"
        return preview
    except Exception as e:
        logger.error(f"保存定时消息失败: {e}")
        session.rollback()
        return "保存定时消息失败，请稍后重试。"
    finally:
        session.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消当前操作"""
    user_id = update.effective_user.id
    if user_id in message_cache:
        del message_cache[user_id]
    
    await update.message.reply_text("操作已取消。")
    return ConversationHandler.END

# 修改定时任务处理函数
async def check_scheduled_messages(context: ContextTypes.DEFAULT_TYPE):
    """检查并发送定时消息"""
    now = datetime.now(BEIJING_TZ)
    session = Session()
    
    try:
        # 获取需要发送的消息
        scheduled_msgs = session.query(ScheduledMessage).filter(
            ScheduledMessage.schedule_time <= now
        ).all()
        
        for msg in scheduled_msgs:
            try:
                # 根据消息类发送不同的消息
                if msg.message_type == 'text':
                    sent_message = await context.bot.send_message(
                        chat_id=msg.group_id,
                        text=msg.message_text
                    )
                elif msg.message_type == 'photo':
                    sent_message = await context.bot.send_photo(
                        chat_id=msg.group_id,
                        photo=msg.message_text
                    )
                elif msg.message_type == 'video':
                    sent_message = await context.bot.send_video(
                        chat_id=msg.group_id,
                        video=msg.message_text
                    )
                
                if msg.delete_time:
                    msg.message_id = sent_message.message_id
                    session.commit()
                else:
                    session.delete(msg)
                    session.commit()
                    
            except Exception as e:
                logger.error(f"发送定时消息失败: {e}")
                
        # 检查需要删除的消息
        to_delete = session.query(ScheduledMessage).filter(
            ScheduledMessage.delete_time <= now,
            ScheduledMessage.message_id.isnot(None)
        ).all()
        
        for msg in to_delete:
            try:
                await context.bot.delete_message(
                    chat_id=msg.group_id,
                    message_id=msg.message_id
                )
                session.delete(msg)
                session.commit()
            except Exception as e:
                logger.error(f"删除定时消息失败: {e}")
                
    finally:
        session.close()

async def manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await check_user_permission(user_id, 'can_manage_groups'):
        await query.answer("您没有权限管理群组！")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ 添加群组", callback_data='group_add')],
        [InlineKeyboardButton("➖ 删除群组", callback_data='group_remove')],
        [InlineKeyboardButton("📋 查看群组列表", callback_data='group_list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("请选择群组管理操作：", reply_markup=reply_markup)

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "请将机器人添加到群组，并赋管理员权限。\n"
        "然后在群组中发送 /register 命令进行注册。"
    )

async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']:
        return
    
    user_id = update.effective_user.id
    if not await check_user_permission(user_id, 'can_manage_groups'):
        await update.message.reply_text("您没有权限注册群组！")
        return
    
    chat = update.effective_chat
    session = Session()
    
    try:
        existing_group = session.query(Group).filter_by(group_id=chat.id).first()
        if existing_group:
            await update.message.reply_text("该群组已经注册！")
            return
        
        new_group = Group(
            group_id=chat.id,
            group_name=chat.title,
            monitoring_enabled=True
        )
        session.add(new_group)
        session.commit()
        await update.message.reply_text("群组注册成功！")
    except Exception as e:
        logger.error(f"注册群组失败: {e}")
        await update.message.reply_text("注册群组失败请稍后重试。")
    finally:
        session.close()

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    session = Session()
    
    try:
        groups = session.query(Group).all()
        if not groups:
            await query.edit_message_text("目前没有注册的群组。")
            return
        
        keyboard = []
        for group in groups:
            monitoring_status = "✅" if group.monitoring_enabled else "❌"
            keyboard.extend([
                [InlineKeyboardButton(
                    f"{group.group_name} {monitoring_status}",
                    callback_data=f'group_settings_{group.group_id}'
                )]
            ])
        
        keyboard.append([InlineKeyboardButton("返回主菜单", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("已注册的群组：\n点击群组名称进行设置", reply_markup=reply_markup)
    finally:
        session.close()

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    session = Session()
    
    try:
        groups = session.query(Group).all()
        keyboard = []
        for group in groups:
            keyboard.append([InlineKeyboardButton(
                group.group_name, 
                callback_data=f'remove_group_{group.group_id}'
            )])
        keyboard.append([InlineKeyboardButton("返回", callback_data='back_to_groups')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("选择要删除的群组：", reply_markup=reply_markup)
    finally:
        session.close()

async def confirm_remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = int(query.data.split('_')[2])
    
    session = Session()
    try:
        group = session.query(Group).filter_by(group_id=group_id).first()
        if group:
            session.delete(group)
            session.commit()
            await query.edit_message_text(f"群 {group.group_name} 已删除！")
        else:
            await query.edit_message_text("群组不存在！")
    except Exception as e:
        logger.error(f"删除群组失败: {e}")
        await query.edit_message_text("删除群组失败，请稍后重试。")
    finally:
        session.close()

async def manage_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await check_user_permission(user_id, 'can_manage_whitelist'):
        await query.answer("您没有权限管理白名单！")
        return
    
    keyboard = [
        [InlineKeyboardButton(" 添加用户", callback_data='whitelist_add')],
        [InlineKeyboardButton("➖ 删除用户", callback_data='whitelist_remove')],
        [InlineKeyboardButton("📋 查看用户列表", callback_data='whitelist_list')],
        [InlineKeyboardButton("⚙️ 修改权限", callback_data='whitelist_permissions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("请选择白名单管理操作：", reply_markup=reply_markup)

async def add_whitelist_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "请发送要添的用户ID，格式如下：\n"
        "/adduser 用户ID"
    )

async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("请提供用户ID！")
        return
        
    try:
        user_id = int(context.args[0])
        session = Session()
        
        existing_user = session.query(User).filter_by(user_id=user_id).first()
        if existing_user:
            await update.message.reply_text("该用户已在白名单中！")
            return
            
        new_user = User(user_id=user_id)
        session.add(new_user)
        session.commit()
        
        await update.message.reply_text("用户已添加到白名单！")
    except ValueError:
        await update.message.reply_text("无效的用户ID！")
    except Exception as e:
        logger.error(f"添加用户失败: {e}")
        await update.message.reply_text("添加用户失败，请稍后重试。")
    finally:
        session.close()

async def manage_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    session = Session()
    
    try:
        users = session.query(User).all()
        keyboard = []
        for user in users:
            keyboard.append([InlineKeyboardButton(
                f"用户 {user.user_id}",
                callback_data=f'perm_user_{user.user_id}'
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("选择要管理的用户：", reply_markup=reply_markup)
    finally:
        session.close()

async def show_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = int(query.data.split('_')[2])
    
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("用户不存在！")
            return
            
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅' if user.can_schedule else '❌'} 定时发送消息",
                callback_data=f'toggle_{user_id}_schedule'
            )],
            [InlineKeyboardButton(
                f"{'✅' if user.can_delete else '❌'} 删除消息",
                callback_data=f'toggle_{user_id}_delete'
            )],
            [InlineKeyboardButton(
                f"{'✅' if user.can_manage_groups else '❌'} 群组管理",
                callback_data=f'toggle_{user_id}_groups'
            )],
            [InlineKeyboardButton(
                f"{'✅' if user.can_monitor else '❌'} 消息监控",
                callback_data=f'toggle_{user_id}_monitor'
            )],
            [InlineKeyboardButton(
                f"{'✅' if user.can_manage_whitelist else '❌'} 白名单管理",
                callback_data=f'toggle_{user_id}_whitelist'
            )],
            [InlineKeyboardButton("返回", callback_data='back_to_whitelist')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"用户 {user_id} 的权限设置：",
            reply_markup=reply_markup
        )
    finally:
        session.close()

# 添加初始化管理员函数
async def init_admin():
    admin_id = int(os.getenv('ADMIN_ID', '7030183171'))
    session = Session()
    
    try:
        admin = session.query(User).filter_by(user_id=admin_id).first()
        if not admin:
            admin = User(
                user_id=admin_id,
                can_schedule=True,
                can_delete=True,
                can_manage_groups=True,
                can_monitor=True,
                can_manage_whitelist=True
            )
            session.add(admin)
            session.commit()
            logger.info(f"管理员(ID: {admin_id})已初始化")
        else:
            # 确保管理员拥有所有权限
            admin.can_schedule = True
            admin.can_delete = True
            admin.can_manage_groups = True
            admin.can_monitor = True
            admin.can_manage_whitelist = True
            session.commit()
            logger.info(f"管理员(ID: {admin_id})权限已更新")
    except Exception as e:
        logger.error(f"初始化管理员失败: {e}")
        session.rollback()
    finally:
        session.close()

# 添加自定义时间选择按钮
async def select_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = []
    now = datetime.now(BEIJING_TZ)
    
    # 添加未来24小时的选项
    for i in range(1, 25):
        future_time = now + timedelta(hours=i)
        time_str = future_time.strftime("%H:00")
        keyboard.append([InlineKeyboardButton(
            f"今天 {time_str}",
            callback_data=f'custom_time_{future_time.timestamp()}'
        )])
    
    # 添加未来7天的选项
    for i in range(1, 8):
        future_date = now + timedelta(days=i)
        date_str = future_date.strftime("%m-%d")
        keyboard.append([InlineKeyboardButton(
            f"{date_str} 00:00",
            callback_data=f'custom_time_{future_date.replace(hour=0, minute=0).timestamp()}'
        )])
    
    keyboard.append([InlineKeyboardButton("返回", callback_data='back_to_time')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("请选择具体时间：", reply_markup=reply_markup)

# 添加群组设置菜单
async def group_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = int(query.data.split('_')[2])
    
    session = Session()
    try:
        group = session.query(Group).filter_by(group_id=group_id).first()
        if not group:
            await query.edit_message_text("群组不存在！")
            return
            
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅' if group.monitoring_enabled else '❌'} 消息监控",
                callback_data=f'toggle_monitor_{group_id}'
            )],
            [InlineKeyboardButton("返回群组列表", callback_data='back_to_groups')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"群组 {group.group_name} 的设置：",
            reply_markup=reply_markup
        )
    finally:
        session.close()

# 添加监控开关处理
async def toggle_group_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = int(query.data.split('_')[2])
    
    session = Session()
    try:
        group = session.query(Group).filter_by(group_id=group_id).first()
        if group:
            group.monitoring_enabled = not group.monitoring_enabled
            session.commit()
            await group_settings(update, context)
    finally:
        session.close()

# 添加错误处理函数
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update.effective_message:
            await update.effective_message.reply_text(
                "抱歉，处理您的请求时出现错误。请稍后重试或联系管理员。"
            )
    except:
        pass

# 修改 web_app 函数
async def web_app():
    app = web.Application()
    
    # 添加一个简单的健康检查路由
    async def health_check(request):
        return web.Response(text="Bot is running")
    
    # 添加 webhook 路由
    async def handle_webhook(request):
        try:
            update = await request.json()
            await application.process_update(Update.de_json(update, application.bot)) # type: ignore
            return web.Response()
        except Exception as e:
            logger.error(f"Error processing update: {e}")
            return web.Response(status=500)
    
    app.router.add_get("/", health_check)
    app.router.add_post("/webhook", handle_webhook)
    return app

# 修改 main 函数
def main():
    # 获取 PORT 环境变量，默认为 10000
    port = int(os.getenv("PORT", "10000"))
    webhook_url = os.getenv("WEBHOOK_URL")
    
    # 创建 application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 初始化管理员
    application.loop.run_until_complete(init_admin())
    
    # 添加定时任务
    job_queue = application.job_queue
    job_queue.run_repeating(check_scheduled_messages, interval=30)
    
    # 定时送消息的会话处理器
    schedule_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(schedule_message, pattern='^schedule_message$')],
        states={
            COLLECTING_MESSAGES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_messages),
                MessageHandler(filters.PHOTO, collect_messages),
                MessageHandler(filters.VIDEO, collect_messages)
            ],
            SELECTING_GROUP: [CallbackQueryHandler(select_group, pattern='^group_')],
            SELECTING_TIME: [
                CallbackQueryHandler(select_time, pattern='^time_'),
                CallbackQueryHandler(select_time, pattern='^custom_time_')
            ],
            CONFIRMING_DELETE: [CallbackQueryHandler(confirm_delete, pattern='^delete_')],
            SELECTING_DELETE_TIME: [CallbackQueryHandler(select_delete_time, pattern='^deltime_')]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CallbackQueryHandler(handle_back, pattern='^back_to_') # type: ignore
        ]
    )
    
    application.add_handler(schedule_conv_handler)
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # 添加群组管理相关的处理器
    application.add_handler(CallbackQueryHandler(manage_groups, pattern='^manage_groups$'))
    application.add_handler(CallbackQueryHandler(add_group, pattern='^group_add$'))
    application.add_handler(CallbackQueryHandler(remove_group, pattern='^group_remove$'))
    application.add_handler(CallbackQueryHandler(list_groups, pattern='^group_list$'))
    application.add_handler(CallbackQueryHandler(confirm_remove_group, pattern='^remove_group_'))
    application.add_handler(CommandHandler('register', register_group))
    
    # 添加白名单管理相关的处理器
    application.add_handler(CallbackQueryHandler(manage_whitelist, pattern='^manage_whitelist$'))
    application.add_handler(CallbackQueryHandler(add_whitelist_user, pattern='^whitelist_add$'))
    application.add_handler(CallbackQueryHandler(manage_user_permissions, pattern='^whitelist_permissions$'))
    application.add_handler(CallbackQueryHandler(show_user_permissions, pattern='^perm_user_'))
    application.add_handler(CommandHandler('adduser', add_user_command))
    
    # 添加自定义时间处理器
    application.add_handler(CallbackQueryHandler(select_custom_time, pattern='^time_custom$'))
    application.add_handler(CallbackQueryHandler(
        lambda u, c: select_time(u, c), 
        pattern='^custom_time_'
    ))
    
    # 添加群组设置处理器
    application.add_handler(CallbackQueryHandler(group_settings, pattern='^group_settings_'))
    application.add_handler(CallbackQueryHandler(toggle_group_monitoring, pattern='^toggle_monitor_'))
    
    # 添加统一的返回处理
    application.add_handler(CallbackQueryHandler(handle_back, pattern='^back_to_')) # type: ignore
    
    # 添加错误处理器
    application.add_error_handler(error_handler)
    
    # 创建 web 应用
    web_app = application.loop.run_until_complete(web_app())
    
    # 启动机器人和web服务器
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_app=web_app,
        webhook_url=f"{webhook_url}/webhook",
        secret_token="your-secret-path"  # 添加一个��钥
    )

if __name__ == '__main__':
    main() 