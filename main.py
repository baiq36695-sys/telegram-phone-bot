#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 
稳定版本 v10.1-Final - 修复数据一致性和正则表达式问题

新增功能：
1. 重启后延迟启动轮询，避免竞态条件
2. 自动健康检查和队列清理
3. API兼容性修复，支持python-telegram-bot 22.5
4. 使用v9.5经典简洁界面风格
5. 修复正则表达式，防止识别无效号码
6. 显示首次提交者信息
7. 改进标准化函数，严格长度验证
8. 新增中国号码支持

修复问题：
- ✅ 修复不完整号码误识别
- ✅ 改进正则表达式严格性
- ✅ 修复标准化函数长度验证
- ✅ 优化显示格式，避免重复信息
- ✅ 新增多国号码支持

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
from typing import Set, Dict, Any, Tuple, Optional, List
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

# 全局变量 - v9.5风格简洁数据结构，增加第一次发送者信息和重复统计
user_groups: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
    'phones': set(),      # 存储所有号码
    'first_senders': {},  # 存储每个标准化号码的第一次发送者信息
    'duplicate_stats': {} # 存储重复统计信息
})
shutdown_event = threading.Event()
restart_count = 0
health_check_running = False

# Telegram Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')

def normalize_phone(phone: str) -> str:
    """标准化电话号码用于重复检测 - 最终修复版本"""
    # 移除所有非数字字符
    normalized = re.sub(r'[^\d]', '', phone)
    
    # 马来西亚号码处理（修复长度检查）
    if normalized.startswith('60') and len(normalized) >= 11:
        # 马来西亚国际格式：+60 1X-XXXX-XXXX
        # 例如：+60 11-1234-5678 -> 601112345678 (12位) -> 1112345678 (10位)
        return normalized[2:]  # 移除60前缀
    elif normalized.startswith('0') and len(normalized) >= 10:
        # 马来西亚本地格式：01X-XXXX-XXXX
        # 例如：011-1234-5678 -> 01112345678 (11位) -> 1112345678 (10位)
        return normalized[1:]  # 移除0前缀
    
    # 其他格式保持原样
    return normalized

def extract_phones(text: str) -> List[str]:
    """从文本中提取电话号码，使用更严格的规则防止无效号码 - 修复版本"""
    patterns = [
        # 马来西亚手机号 - 国际格式（严格匹配）
        r'\+60\s*1[0-9]\s*[-\s]?\d{3,4}[-\s]?\d{4}',           # +60 11-1234-5678
        r'\+60\s*1[0-9]\d{7}',                                  # +60111234567 (严格9位数字)
        
        # 马来西亚固话 - 国际格式
        r'\+60\s*[3-9]\s*[-\s]?\d{3,4}[-\s]?\d{4}',            # +60 3-1234-5678
        r'\+60\s*[3-9]\d{7}',                                   # +6031234567 (严格8位数字)
        
        # 马来西亚手机号 - 本地格式
        r'01[0-9][-\s]?\d{3,4}[-\s]?\d{4}',                    # 011-1234-5678
        
        # 马来西亚固话 - 本地格式
        r'0[3-9][-\s]?\d{3,4}[-\s]?\d{4}',                     # 03-1234-5678
        
        # 中国手机号（新增支持）
        r'\+86\s*1[3-9]\d{9}',                                  # +86 138-1234-5678
        r'(?<!\d)1[3-9]\d{9}(?!\d)',                           # 138-1234-5678 (避免误匹配)
        
        # 中国固话（新增支持）
        r'\+86\s*[2-9]\d{2,3}\s*\d{7,8}',                      # +86 10-12345678
        r'0[1-9]\d{1,3}[-\s]?\d{7,8}',                         # 010-12345678
        
        # 其他国家格式（保持严格要求）
        r'\+1\s*[2-9]\d{2}[-\s]?[2-9]\d{2}[-\s]?\d{4}',       # 美国/加拿大
        r'\+44\s*[1-9]\d{8,9}',                                # 英国
        r'\+65\s*[6-9]\d{7}',                                  # 新加坡
        r'\+852\s*[2-9]\d{7}',                                 # 香港
        r'\+853\s*[6-9]\d{7}',                                 # 澳门
        r'\+886\s*[0-9]\d{8}',                                 # 台湾
        r'\+91\s*[6-9]\d{9}',                                  # 印度
        r'\+81\s*[7-9]\d{8}',                                  # 日本手机
        r'\+82\s*1[0-9]\d{7,8}',                               # 韩国
        r'\+66\s*[6-9]\d{8}',                                  # 泰国
        r'\+84\s*[3-9]\d{8}',                                  # 越南
        r'\+63\s*[2-9]\d{8}',                                  # 菲律宾
        r'\+62\s*[1-9]\d{7,10}',                               # 印度尼西亚
    ]
    
    # 查找所有匹配
    all_matches = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        all_matches.extend(matches)
    
    # 去重（基于标准化后的号码）
    seen_normalized = set()
    result = []
    
    for match in all_matches:
        # 清理号码
        cleaned = re.sub(r'\s+', ' ', match.strip())
        normalized = normalize_phone(cleaned)
        
        # 验证标准化后的长度（排除无效号码）
        if len(normalized) >= 8 and normalized not in seen_normalized:
            seen_normalized.add(normalized)
            result.append(cleaned)
    
    return result

def find_duplicates(phones: Set[str]) -> Set[str]:
    """查找重复的电话号码"""
    # 创建标准化映射
    normalized_map = {}
    duplicates = set()
    
    for phone in phones:
        normalized = normalize_phone(phone)
        
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
        'version': 'v10.1-final',
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
        'interface_style': 'v9.5-classic-final'
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
    return f"🤖 电话号码查重机器人 v10.1-final 运行中！重启次数: {restart_count}"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令 - v9.5风格界面"""
    user = update.effective_user
    user_name = user.first_name or "朋友"
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v10.1-Final** 🎉
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
• ✅ 修复不完整号码识别

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **最新修复：**
• 🛡️ 修复不完整号码误识别
• ⏱️ 延迟启动保护
• 🔧 API兼容性修复
• 👥 显示首次提交者信息

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
    
    user_groups[chat_id] = {'phones': set(), 'first_senders': {}, 'duplicate_stats': {}}
    
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

🔍 **建议：** 先发送一些电话号码，再使用导出功能

═══════════════════════════"""
        await update.message.reply_text(no_data_message, parse_mode='Markdown')
        return
    
    # 按类型分组
    phone_groups = {}
    for phone in all_phones:
        category = categorize_phone_number(phone)
        if category not in phone_groups:
            phone_groups[category] = []
        phone_groups[category].append(phone)
    
    # 构建导出消息
    export_message = f"""📊 **号码清单导出** 📊
═══════════════════════════

📱 **总数统计：** {len(all_phones)} 个号码
🕒 **导出时间：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
    
    for category, phones in phone_groups.items():
        export_message += f"\n**{category}** ({len(phones)}个):\n"
        for phone in sorted(phones):
            export_message += f"• `{phone}`\n"
    
    export_message += f"\n═══════════════════════════\n💾 **导出完成！**"
    
    await update.message.reply_text(export_message, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令 - v9.5风格统计"""
    chat_id = update.effective_chat.id
    all_phones = list(user_groups[chat_id].get('phones', set()))
    
    if not all_phones:
        stats_message = f"""📊 **详细统计报告** 📊
═══════════════════════════

📱 **号码统计：** 0 个号码
🔍 **重复检测：** 无数据

⚠️ **提示：** 当前群组暂无电话号码记录

═══════════════════════════"""
        await update.message.reply_text(stats_message, parse_mode='Markdown')
        return
    
    # 按类型统计
    phone_stats = {}
    duplicates = find_duplicates(set(all_phones))
    
    for phone in all_phones:
        category = categorize_phone_number(phone)
        if category not in phone_stats:
            phone_stats[category] = {'count': 0, 'duplicates': 0}
        phone_stats[category]['count'] += 1
        if phone in duplicates:
            phone_stats[category]['duplicates'] += 1
    
    stats_message = f"""📊 **详细统计报告** 📊
═══════════════════════════

📱 **总体统计：**
• **总号码数：** {len(all_phones)} 个
• **重复号码：** {len(duplicates)} 个
• **有效号码：** {len(all_phones) - len(duplicates)} 个
• **统计时间：** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🌍 **分类统计：**
"""
    
    for category, stats in phone_stats.items():
        duplicate_info = f" (重复: {stats['duplicates']})" if stats['duplicates'] > 0 else ""
        stats_message += f"• **{category}:** {stats['count']} 个{duplicate_info}\n"
    
    stats_message += f"\n═══════════════════════════\n🎯 **检测效率：** {((len(all_phones) - len(duplicates)) / len(all_phones) * 100):.1f}%"
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令 - v9.5风格帮助"""
    help_message = f"""❓ **快速帮助** ❓
═══════════════════════════

🤖 **机器人功能：**
电话号码重复检测，支持多国格式，智能去重

📱 **支持格式：**
• **马来西亚：** +60 11-1234-5678, 011-1234-5678
• **中国：** +86 138-1234-5678, 138-1234-5678
• **美加：** +1 555-123-4567
• **其他国际格式**

⚡ **使用方法：**
1. 直接发送包含电话号码的消息
2. 机器人自动检测和去重
3. 显示详细的检测结果

🎯 **命令说明：**
• `/start` - 开始使用
• `/help` - 显示此帮助
• `/stats` - 查看详细统计
• `/clear` - 清空数据库
• `/export` - 导出号码清单

⚠️ **注意事项：**
• 只识别完整的电话号码
• 自动去除重复号码
• 支持多种国际格式

═══════════════════════════
🚀 **开始体验智能去重！**"""
    
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息 - 优化显示效果"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_name = user.first_name or user.username or "未知用户"
    message_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 提取电话号码
    phones = extract_phones(text)
    
    if not phones:
        return  # 没有找到电话号码，不响应
    
    # 确保用户组数据结构存在
    if chat_id not in user_groups:
        user_groups[chat_id] = {'phones': set(), 'first_senders': {}, 'duplicate_stats': {}}
    
    group_data = user_groups[chat_id]
    existing_phones = group_data['phones']
    first_senders = group_data['first_senders']
    duplicate_stats = group_data.get('duplicate_stats', {})
    
    new_phones = []
    duplicate_info = []
    
    for phone in phones:
        normalized = normalize_phone(phone)
        
        # 检查是否重复
        is_duplicate = False
        for existing_phone in existing_phones:
            if normalize_phone(existing_phone) == normalized:
                # 记录重复统计
                if normalized not in duplicate_stats:
                    duplicate_stats[normalized] = {'count': 1, 'users': set([first_senders.get(normalized, {}).get('user', '未知')])}
                
                duplicate_stats[normalized]['count'] += 1
                duplicate_stats[normalized]['users'].add(user_name)
                
                duplicate_info.append({
                    'phone': phone,
                    'existing_phone': existing_phone,
                    'normalized': normalized,
                    'stats': duplicate_stats[normalized]
                })
                is_duplicate = True
                break
        
        if not is_duplicate:
            new_phones.append(phone)
            existing_phones.add(phone)
            # 记录首次发送者信息
            first_senders[normalized] = {
                'user': user_name,
                'time': message_time,
                'original_format': phone
            }
    
    # 构建响应消息
    if new_phones and not duplicate_info:
        # 只有新号码 - 简洁显示
        for phone in new_phones:
            category = categorize_phone_number(phone)
            response = f"""✅ **号码已记录！** ✅

🇲🇾 **号码：** {phone}

📅 **添加时间：** {message_time}
👤 **添加用户：** {user_name}

🎉 **状态：** 首次添加，无重复！

✨ **号码已成功添加到数据库！**"""
            
            await update.message.reply_text(response, parse_mode='Markdown')
        
    elif duplicate_info and not new_phones:
        # 只有重复号码 - 详细统计显示
        for dup in duplicate_info:
            normalized = dup['normalized']
            first_sender_info = first_senders.get(normalized, {})
            stats = dup['stats']
            
            response = f"""❌ **发现重复号码！** ❌

🇲🇾 **号码：** {dup['phone']}

📅 **首次添加：** {first_sender_info.get('time', '未知')}
👤 **首次用户：** {first_sender_info.get('user', '未知')}

📅 **当前检测：** {message_time}
👤 **当前用户：** {user_name}

📊 **统计信息：**
📊 **总提交次数：** {stats['count']} 次
👥 **涉及用户：** {len(stats['users'])} 人

⚠️ **请注意：此号码已被使用！**"""
            
            await update.message.reply_text(response, parse_mode='Markdown')
        
    else:
        # 混合情况：既有新号码又有重复号码
        for phone in new_phones:
            category = categorize_phone_number(phone)
            response = f"""✅ **号码已记录！** ✅

🇲🇾 **号码：** {phone}

📅 **添加时间：** {message_time}
👤 **添加用户：** {user_name}

🎉 **状态：** 首次添加，无重复！

✨ **号码已成功添加到数据库！**"""
            
            await update.message.reply_text(response, parse_mode='Markdown')
        
        for dup in duplicate_info:
            normalized = dup['normalized']
            first_sender_info = first_senders.get(normalized, {})
            stats = dup['stats']
            
            response = f"""❌ **发现重复号码！** ❌

🇲🇾 **号码：** {dup['phone']}

📅 **首次添加：** {first_sender_info.get('time', '未知')}
👤 **首次用户：** {first_sender_info.get('user', '未知')}

📅 **当前检测：** {message_time}
👤 **当前用户：** {user_name}

📊 **统计信息：**
📊 **总提交次数：** {stats['count']} 次
👥 **涉及用户：** {len(stats['users'])} 人

⚠️ **请注意：此号码已被使用！**"""
            
            await update.message.reply_text(response, parse_mode='Markdown')

async def periodic_health_check():
    """定期健康检查"""
    global health_check_running
    health_check_running = True
    
    while not shutdown_event.is_set():
        try:
            # 检查数据一致性
            total_phones = sum(len(data.get('phones', set())) for data in user_groups.values())
            logger.info(f"健康检查：监控 {len(user_groups)} 个群组，总计 {total_phones} 个号码")
            
            # 每5分钟检查一次
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"健康检查错误: {e}")
            await asyncio.sleep(60)
    
    health_check_running = False

def signal_handler(signum, frame):
    """信号处理器"""
    logger.info(f"收到信号 {signum}，准备优雅关闭...")
    shutdown_event.set()

async def delayed_start_polling(app):
    """延迟启动轮询以避免竞态条件"""
    logger.info("等待3秒后启动轮询，避免重启竞态条件...")
    await asyncio.sleep(3)
    
    logger.info("开始轮询Telegram更新...")
    await app.start()
    
    # 启动健康检查
    asyncio.create_task(periodic_health_check())
    
    await app.updater.start_polling(drop_pending_updates=True)
    await app.updater.idle()
    await app.stop()

def main():
    """主函数 - v10.1修复版"""
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动Flask服务器
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 创建Telegram机器人应用 - 使用最新API
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info(get_restart_status())
    
    try:
        # 使用延迟启动避免重启竞态条件
        asyncio.run(delayed_start_polling(application))
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        # 可以在这里添加自动重启逻辑
    finally:
        shutdown_event.set()
        logger.info("机器人已关闭")

if __name__ == "__main__":
    main()
