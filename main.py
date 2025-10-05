#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 
稳定版本 v10.1 - API兼容性修复，v9.5经典界面风格

新增功能：
1. 重启后延迟启动轮询，避免竞态条件
2. 自动健康检查和队列清理
3. API兼容性修复，支持python-telegram-bot 22.5
4. 使用v9.5经典简洁界面风格

作者: MiniMax Agent
"""

import os
import re
import logging
import signal
import sys
import asyncio
import datetime
import time
import threading
import json
from typing import Set, Dict, Any, Tuple, Optional
from collections import defaultdict

# 首先安装并应用nest_asyncio来解决事件循环冲突
try:
    import nest_asyncio
    nest_asyncio.apply()
    print("✅ nest_asyncio已应用，事件循环冲突已解决")
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "nest-asyncio"])
    import nest_asyncio
    nest_asyncio.apply()

# 导入相关库
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 禁用HTTP请求的详细日志，只保留机器人重要信息
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('telegram.request').setLevel(logging.WARNING)
logging.getLogger('telegram.vendor.ptb_urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)

# 全局变量 - v9.5风格简洁数据结构，增加第一次发送者信息
user_groups: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
    'phones': set(),      # 存储所有号码
    'first_senders': {}   # 存储每个标准化号码的第一次发送者信息
})
shutdown_event = threading.Event()
restart_count = 0
health_check_running = False

# Telegram Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码 - 支持多国格式，特别优化马来西亚格式"""
    patterns = [
        # 国际格式优先（这些会被优先处理）
        r'\+60\s*1[0-9](?:\s*[-\s]?\s*\d{4}\s*[-\s]?\s*\d{4}|\d{7})',  # +60 11-2896 2309 或 +60112896309
        r'\+60\s*[3-9](?:\s*[-\s]?\s*\d{4}\s*[-\s]?\s*\d{4}|\d{7,8})', # +60 3-1234 5678 (固话)
        r'\+86\s*1[3-9]\d{9}',                       # 中国手机
        r'\+86\s*[2-9]\d{2,3}\s*\d{7,8}',           # 中国固话
        r'\+1\s*[2-9]\d{2}\s*[2-9]\d{2}\s*\d{4}',   # 美国/加拿大
        r'\+44\s*[1-9]\d{8,9}',                     # 英国
        r'\+65\s*[6-9]\d{7}',                       # 新加坡
        r'\+852\s*[2-9]\d{7}',                      # 香港
        r'\+853\s*[6-9]\d{7}',                      # 澳门
        r'\+886\s*[0-9]\d{8}',                      # 台湾
        r'\+91\s*[6-9]\d{9}',                       # 印度
        r'\+81\s*[7-9]\d{8}',                       # 日本手机
        r'\+82\s*1[0-9]\d{7,8}',                    # 韩国
        r'\+66\s*[6-9]\d{8}',                       # 泰国
        r'\+84\s*[3-9]\d{8}',                       # 越南
        r'\+63\s*[2-9]\d{8}',                       # 菲律宾
        r'\+62\s*[1-9]\d{7,10}',                    # 印度尼西亚
        r'\+\d{1,4}\s*\d{1,4}\s*\d{1,4}\s*\d{1,9}', # 通用国际格式
        
        # 本地格式（无国际代码）
        r'1[3-9]\d{9}',                             # 中国手机（本地）
        r'0[1-9]\d{1,3}[-\s]?\d{7,8}',             # 中国固话（本地）
        r'01[0-9][-\s]?\d{4}[-\s]?\d{4}',          # 马来西亚手机（本地）
        r'0[3-9][-\s]?\d{4}[-\s]?\d{4}',           # 马来西亚固话（本地）
    ]
    
    phone_numbers = set()
    normalized_numbers = set()  # 用于去重
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # 清理电话号码：移除多余空格，但保留格式
            cleaned = re.sub(r'\s+', ' ', match.strip())
            
            # 标准化用于去重检查
            normalized = re.sub(r'[^\d+]', '', cleaned)
            
            # 如果这个标准化号码还没有被添加过，则添加
            if normalized not in normalized_numbers:
                phone_numbers.add(cleaned)
                normalized_numbers.add(normalized)
    
    return phone_numbers

def find_duplicates(phones: Set[str]) -> Set[str]:
    """查找重复的电话号码"""
    # 创建标准化映射
    normalized_map = {}
    duplicates = set()
    
    for phone in phones:
        # 标准化：移除所有空格、连字符等格式字符，只保留数字和+号
        normalized = re.sub(r'[^\d+]', '', phone)
        
        if normalized in normalized_map:
            # 发现重复，添加原始格式和已存在的格式
            duplicates.add(phone)
            duplicates.add(normalized_map[normalized])
        else:
            normalized_map[normalized] = phone
    
    return duplicates

def categorize_phone_number(phone: str) -> str:
    """分类电话号码并返回详细信息"""
    if phone.startswith('+60'):
        if re.match(r'\+60\s*1[0-9]', phone):
            return "🇲🇾 马来西亚手机"
        else:
            return "🇲🇾 马来西亚固话"
    elif phone.startswith('+86'):
        if re.match(r'\+86\s*1[3-9]', phone):
            return "🇨🇳 中国手机"
        else:
            return "🇨🇳 中国固话"
    elif phone.startswith('+1'):
        return "🇺🇸 美加地区"
    elif phone.startswith('+44'):
        return "🇬🇧 英国"
    elif phone.startswith('+65'):
        return "🇸🇬 新加坡"
    elif phone.startswith('+852'):
        return "🇭🇰 香港"
    elif phone.startswith('+853'):
        return "🇲🇴 澳门"
    elif phone.startswith('+886'):
        return "🇹🇼 台湾"
    elif phone.startswith('+91'):
        return "🇮🇳 印度"
    elif phone.startswith('+81'):
        return "🇯🇵 日本"
    elif phone.startswith('+82'):
        return "🇰🇷 韩国"
    elif phone.startswith('+66'):
        return "🇹🇭 泰国"
    elif phone.startswith('+84'):
        return "🇻🇳 越南"
    elif phone.startswith('+63'):
        return "🇵🇭 菲律宾"
    elif phone.startswith('+62'):
        return "🇮🇩 印度尼西亚"
    elif phone.startswith('01'):
        return "🇲🇾 马来西亚本地手机"
    elif phone.startswith('0'):
        return "🇲🇾 马来西亚本地固话"
    elif re.match(r'^1[3-9]\d{9}$', phone):
        return "🇨🇳 中国本地手机"
    else:
        return "🌍 其他地区"

# Flask路由
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'service': 'telegram-phone-bot',
        'version': 'v10.1-classic',
        'restart_count': restart_count,
        'health_check_active': health_check_running,
        'timestamp': time.time()
    })

@app.route('/status')
def status():
    """状态端点"""
    total_phones = sum(len(data.get('phones', set())) for data in user_groups.values())
    return jsonify({
        'bot_status': 'running' if not shutdown_event.is_set() else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': total_phones,
        'restart_count': restart_count,
        'health_check_active': health_check_running,
        'interface_style': 'v9.5-classic'
    })

def run_flask():
    """运行Flask服务器"""
    try:
        port = int(os.environ.get('PORT', 10000))
        logger.info(f"Flask服务器启动，端口: {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask服务器启动失败: {e}")

def get_restart_status():
    """获取重启状态信息"""
    global restart_count
    restart_count += 1
    return f"🤖 电话号码查重机器人 v10.1 运行中！重启次数: {restart_count}"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令 - v9.5风格界面"""
    user = update.effective_user
    user_name = user.first_name or "朋友"
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v10.1** 🎉
═══════════════════════════

👋 欢迎，**{user_name}**！

🔍 **功能特点：**
• 智能去重检测
• 自动重启保护
• 队列健康检查
• 多国格式识别
• 重复次数统计
• 📊 完整统计功能
• 🔄 稳定自动重启

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **新增功能：**
• 🛡️ 智能重启检测
• ⏱️ 延迟启动保护
• 🔧 API兼容性修复

**命令列表：**
• `/help` - 快速帮助
• `/stats` - 查看详细统计
• `/clear` - 清空数据库
• `/export` - 导出号码清单

═══════════════════════════
🚀 开始发送电话号码吧！
"""
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令 - v9.5风格"""
    chat_id = update.effective_chat.id
    old_count = len(user_groups[chat_id].get('phones', set()))
    
    user_groups[chat_id] = {'phones': set(), 'first_senders': {}}
    
    clear_message = f"""🗑️ **数据库已清空！** 🗑️
═══════════════════════════

📊 **清理统计：**
• **已删除号码：** {old_count} 个
• **当前状态：** 数据库为空
• **清理时间：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

═══════════════════════════
✨ **可以重新开始记录号码了！**"""
    
    await update.message.reply_text(clear_message, parse_mode='Markdown')

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新增 /export 命令 - v9.5风格导出"""
    chat_id = update.effective_chat.id
    all_phones = list(user_groups[chat_id].get('phones', set()))
    
    if not all_phones:
        no_data_message = f"""📝 **数据导出** 📝
═══════════════════════════

⚠️ **提示：** 当前群组暂无电话号码记录

═══════════════════════════
💡 **发送号码后再尝试导出！**"""
        await update.message.reply_text(no_data_message, parse_mode='Markdown')
        return
    
    # 按类型分组
    phone_by_type = {}
    for phone in all_phones:
        phone_type = categorize_phone_number(phone)
        if phone_type not in phone_by_type:
            phone_by_type[phone_type] = []
        phone_by_type[phone_type].append(phone)
    
    export_text = f"""📋 **号码清单导出** 📋
═══════════════════════════

📊 **总计：** {len(all_phones)} 个号码

"""
    
    for phone_type, phones in sorted(phone_by_type.items()):
        export_text += f"**{phone_type}** ({len(phones)}个):\n"
        for i, phone in enumerate(sorted(phones), 1):
            export_text += f"{i:2d}. `{phone}`\n"
        export_text += "\n"
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    export_text += f"""═══════════════════════════
📅 **导出时间：** {now}"""
    
    await update.message.reply_text(export_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令处理 - v9.5风格"""
    help_message = f"""
🆘 **快速帮助** - v10.1
═══════════════════════════

📋 **可用命令：**
• `/start` - 完整功能介绍
• `/help` - 快速帮助（本页面）
• `/stats` - 详细统计信息
• `/clear` - 清空数据库
• `/export` - 导出号码清单

📱 **使用方法：**
直接发送电话号码给我即可自动检测！

⭐ **新功能：**
• 🛡️ 智能重启保护
• ⏱️ 延迟启动防护
• 🔧 API兼容性修复

═══════════════════════════
💡 直接发送号码开始使用！
"""
    
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令 - v9.5风格"""
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "私聊"
    user_name = update.effective_user.first_name or "用户"
    
    # 获取所有号码数据
    all_phones = list(user_groups[chat_id].get('phones', set()))
    
    # 按国家分类统计
    country_stats = {}
    for phone in all_phones:
        country = categorize_phone_number(phone)
        country_stats[country] = country_stats.get(country, 0) + 1
    
    # 计算统计
    total_count = len(all_phones)
    malaysia_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇲🇾")])
    china_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇨🇳")])
    
    # 构建国家统计文本
    country_text = "🌍 **国家分布：**"
    if country_stats:
        for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
            percentage = count/max(total_count,1)*100
            country_text += f"\n• {country}: {count} 个 ({percentage:.1f}%)"
    else:
        country_text += "\n• 暂无数据"
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    stats_message = f"""📊 **统计报告** - v10.1
═══════════════════════════

👤 **查询者：** {user_name}
🏠 **群组：** {chat_title}
📅 **查询时间：** {now}

📈 **总体统计：**
• **总电话号码：** {total_count} 个
• **马来西亚号码：** {malaysia_count} 个
• **中国号码：** {china_count} 个

{country_text}

⚙️ **运行状态：**
• 🔄 重启次数：{restart_count}
• 🛡️ 健康检查：已启用

═══════════════════════════
💡 使用 `/clear` 清空数据库"""
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

def normalize_phone_number(phone: str) -> str:
    """标准化电话号码用于重复检测"""
    # 移除所有非数字和+号字符
    normalized = re.sub(r'[^\d+]', '', phone)
    
    # 处理马来西亚号码的标准化
    if normalized.startswith('+60'):
        # +60 转换为标准格式：去掉+60前缀，保留后续数字
        # +6011xxxxxxxx -> 11xxxxxxxx
        # +603xxxxxxxx -> 3xxxxxxxx
        return normalized[3:]  # 移除 +60
    elif normalized.startswith('60') and len(normalized) >= 10:
        # 处理可能缺少+号的情况：60xxxxxxxxx -> xxxxxxxxx
        return normalized[2:]  # 移除 60
    elif normalized.startswith('0') and len(normalized) >= 9:
        # 本地格式：011xxxxxxxx -> 11xxxxxxxx，03xxxxxxxx -> 3xxxxxxxx
        return normalized[1:]  # 移除前导 0
    
    # 处理中国号码的标准化
    if normalized.startswith('+86'):
        # +86 转换为标准格式：去掉+86前缀
        return normalized[3:]  # 移除 +86
    elif normalized.startswith('86') and len(normalized) >= 13:
        # 处理可能缺少+号的情况：86xxxxxxxxxxx -> xxxxxxxxxxx
        return normalized[2:]  # 移除 86
    
    # 其他情况保持原样
    return normalized

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息 - v9.5风格界面，增加第一次发送者信息"""
    try:
        text = update.message.text
        chat_id = update.effective_chat.id
        user = update.effective_user
        current_time = datetime.datetime.now()
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(text)
        
        if not phone_numbers:
            return  # 如果没有电话号码，不回复
        
        # 初始化聊天数据
        if chat_id not in user_groups:
            user_groups[chat_id] = {'phones': set(), 'first_senders': {}}
        
        # 确保数据结构完整性
        if 'phones' not in user_groups[chat_id]:
            user_groups[chat_id]['phones'] = set()
        if 'first_senders' not in user_groups[chat_id]:
            user_groups[chat_id]['first_senders'] = {}
        
        all_user_phones = user_groups[chat_id]['phones']
        first_senders = user_groups[chat_id]['first_senders']
        
        for phone in phone_numbers:
            # 使用改进的标准化函数检查重复
            normalized_new = normalize_phone_number(phone)
            is_duplicate = False
            first_sender_info = None
            
            # 检查是否存在重复
            if normalized_new in first_senders:
                is_duplicate = True
                first_sender_info = first_senders[normalized_new]
            
            country_flag = categorize_phone_number(phone).split(' ')[0]  # 获取国旗
            
            if is_duplicate:
                # 发现重复号码 - v9.5风格，显示第一次发送者
                first_user = first_sender_info['user']
                first_time = first_sender_info['time']
                original_phone = first_sender_info['original_phone']
                
                duplicate_message = f"""🚨 **发现重复号码！** 🚨
═══════════════════════════

{country_flag} **号码：** `{phone}`

📅 **当前检测：** {current_time.strftime('%Y-%m-%d %H:%M:%S')}
👤 **当前用户：** {user.full_name}

📊 **首次记录信息：**
• 👤 **首次发送者：** {first_user}
• 📅 **首次时间：** {first_time}
• 📱 **原始格式：** `{original_phone}`

═══════════════════════════
⚠️ 请注意：此号码已被使用过！"""
                await update.message.reply_text(duplicate_message, parse_mode='Markdown')
            else:
                # 首次添加号码 - v9.5风格，记录发送者信息
                user_groups[chat_id]['phones'].add(phone)
                user_groups[chat_id]['first_senders'][normalized_new] = {
                    'user': user.full_name,
                    'time': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'original_phone': phone
                }
                
                success_message = f"""✅ **号码已记录！** ✅
═══════════════════════════

{country_flag} **号码：** `{phone}`

📅 **添加时间：** {current_time.strftime('%Y-%m-%d %H:%M:%S')}
👤 **添加用户：** {user.full_name}

🎯 **状态：** 首次添加，无重复！

═══════════════════════════
✨ 号码已成功加入数据库！"""
                await update.message.reply_text(success_message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text("❌ 处理消息时出现错误，请稍后重试")

# 信号处理器
def signal_handler(signum, frame):
    """处理关闭信号"""
    logger.info(f"收到信号 {signum}，开始优雅关闭...")
    shutdown_event.set()

# === 新增 v10.1 功能：队列健康检查和清理 ===

async def check_message_queue_status() -> int:
    """检查消息队列状态，返回待处理消息数量"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return data.get('result', {}).get('pending_update_count', 0)
        
        logger.warning(f"检查队列状态失败: {response.status_code}")
        return -1
        
    except Exception as e:
        logger.error(f"检查队列状态异常: {e}")
        return -1

async def clear_message_queue() -> bool:
    """清理消息队列"""
    try:
        # 先获取当前更新以找到最新的update_id
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                updates = data.get('result', [])
                if updates:
                    # 找到最高的update_id
                    max_update_id = max(update['update_id'] for update in updates)
                    
                    # 使用offset清理队列
                    clear_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
                    clear_params = {'offset': max_update_id + 1, 'timeout': 1}
                    
                    clear_response = requests.get(clear_url, params=clear_params, timeout=5)
                    
                    # 504超时是正常的，表示队列已清空
                    if clear_response.status_code in [200, 504]:
                        logger.info("✅ 消息队列清理成功")
                        return True
                else:
                    logger.info("✅ 消息队列已是空的")
                    return True
        
        logger.warning("清理消息队列失败")
        return False
        
    except Exception as e:
        logger.error(f"清理消息队列异常: {e}")
        return False

async def intelligent_queue_check_and_clear():
    """智能队列检测和清理"""
    logger.info("🔍 开始智能队列检测...")
    
    pending_count = await check_message_queue_status()
    
    if pending_count > 0:
        logger.warning(f"⚠️ 发现 {pending_count} 条待处理消息，开始清理...")
        success = await clear_message_queue()
        if success:
            # 再次确认
            final_count = await check_message_queue_status()
            if final_count == 0:
                logger.info("✅ 队列清理完成，状态正常")
            else:
                logger.warning(f"⚠️ 清理后仍有 {final_count} 条消息")
        else:
            logger.error("❌ 队列清理失败")
    else:
        logger.info("✅ 消息队列状态正常")

def health_check_and_clear_queue():
    """健康检查和队列清理 - 在单独线程中运行"""
    global health_check_running
    health_check_running = True
    
    while not shutdown_event.is_set():
        try:
            # 每30分钟检查一次
            time.sleep(30 * 60)  
            
            if shutdown_event.is_set():
                break
                
            logger.info("🔄 执行定期健康检查...")
            
            # 在新的事件循环中运行异步函数
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(intelligent_queue_check_and_clear())
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"健康检查出错: {e}")
    
    health_check_running = False
    logger.info("🔄 健康检查线程已停止")

def health_check_task():
    """健康检查任务入口"""
    health_check_and_clear_queue()

async def run_bot():
    """运行机器人主程序 - v10.1兼容版"""
    global restart_count
    
    try:
        # 启动时队列清理
        restart_status = "🧠 检测到重启，执行智能清理流程..." if restart_count > 0 else "🚀 首次启动，执行标准清理..."
        logger.info(restart_status)
        
        # v10.1 新特性：重启延迟
        if restart_count > 0:
            delay = 3  # 重启后延迟3秒
            logger.info("⏳ 重启延迟：等待系统稳定...")
            await asyncio.sleep(delay)
        
        # 执行智能队列检测和清理
        await intelligent_queue_check_and_clear()
        
        if restart_count > 0:
            logger.info("✅ 智能清理成功，继续启动流程")
            # 清理后再延迟一点确保稳定
            logger.info("⏳ 清理后延迟：确保队列状态稳定...")
            await asyncio.sleep(2)
        
        # 创建应用程序
        logger.info("开始创建应用程序...")
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 注册处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("clear", clear_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("export", export_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("应用程序创建成功，处理器已注册")
        
        restart_count += 1
        logger.info(f"🎯 电话号码查重机器人 v10.1 启动成功！重启次数: {restart_count}")
        
        # 初始化应用程序
        logger.info("🚀 开始初始化应用程序...")
        await application.initialize()
        
        # 启动应用程序
        logger.info("🚀 开始启动应用程序...")
        await application.start()
        
        # v10.1 新特性：轮询前延迟
        logger.info("🚀 准备启动轮询...")
        if restart_count > 1:  # 不是第一次启动
            delay = 5  # 重启后额外延迟5秒
            logger.info("⏳ 重启后轮询延迟：确保系统完全就绪...")
            await asyncio.sleep(delay)
        
        logger.info("🚀 开始轮询...")
        
        # 启动轮询 - 兼容版本配置
        await application.updater.start_polling(
            drop_pending_updates=True,    # 丢弃待处理更新
            bootstrap_retries=5,          # 增加重试次数
        )
        
        logger.info("✅ 轮询已启动，机器人正在监听消息...")
        
        # 启动后最终确认
        await asyncio.sleep(2)
        final_status = await check_message_queue_status()
        logger.info(f"📊 启动完成，队列状态: {final_status} 条待处理消息")
        
        # 等待关闭信号
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"运行机器人时出错: {e}")
        logger.error(f"详细错误: {str(e)}")
        raise e
    finally:
        # 清理资源
        logger.info("🧹 开始清理资源...")
        try:
            if 'application' in locals():
                logger.info("🧹 停止updater...")
                await application.updater.stop()
                logger.info("🧹 停止应用程序...")
                await application.stop()
                logger.info("🧹 关闭应用程序...")
                await application.shutdown()
        except Exception as e:
            logger.error(f"关闭时出错: {e}")

def main():
    """主函数 - v10.1兼容版"""
    global restart_count
    
    logger.info("=== 电话号码查重机器人 v10.1 启动 (经典界面版) ===")
    logger.info(f"启动时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 启动Flask服务器
        port = int(os.environ.get('PORT', 10000))
        logger.info(f"Flask服务器启动，端口: {port}")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("Flask服务器线程已启动")
        
        # 启动健康检查线程
        health_thread = threading.Thread(target=health_check_task, daemon=True)
        health_thread.start()
        logger.info("🔄 健康检查线程已启动")
        
        # 失败重试逻辑
        max_retries = 10
        retry_count = 0
        retry_delays = [2, 2, 5, 10, 20, 30, 60, 120, 300, 600]  # 递增延迟
        
        while retry_count < max_retries and not shutdown_event.is_set():
            try:
                retry_count += 1
                logger.info(f"=== 第 {retry_count} 次启动机器人 ===")
                
                logger.info("🔄 开始运行机器人...")
                asyncio.run(run_bot())
                
                # 如果到这里说明正常退出
                logger.info("✅ 机器人正常退出")
                break
                
            except Exception as e:
                logger.error(f"=== Bot异常停止 （第{retry_count}次） ===")
                logger.error(f"异常类型: {type(e).__name__}")
                logger.error(f"异常信息: {str(e)}")
                logger.error(f"连续失败: {retry_count} 次")
                
                import traceback
                logger.error(f"详细堆栈: {traceback.format_exc()}")
                
                if retry_count >= max_retries:
                    logger.error("❌ 达到最大重试次数，程序退出")
                    break
                    
                if shutdown_event.is_set():
                    logger.info("🛑 收到关闭信号，停止重试")
                    break
                
                # 计算延迟时间
                delay = retry_delays[min(retry_count-1, len(retry_delays)-1)]
                logger.info(f"⏱️ 失败重启延迟: {delay} 秒...")
                time.sleep(delay)
        
    except KeyboardInterrupt:
        logger.info("👋 用户中断，程序退出")
    except Exception as e:
        logger.error(f"🚨 程序运行异常: {e}")
    finally:
        shutdown_event.set()
        logger.info("🏁 程序执行完毕")

if __name__ == '__main__':
    main()
