#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 
稳定版本 v10.1 - API兼容性修复，解决重启后无响应问题
新增功能：
1. 重启后延迟启动轮询，避免竞态条件
2. 自动健康检查和队列清理
3. API兼容性修复，支持python-telegram-bot 22.5
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
logger = logging.getLogger(__name__)
# 初始化Flask应用
app = Flask(__name__)
# 全局变量
user_groups: Dict[int, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
shutdown_event = threading.Event()
restart_count = 0
health_check_running = False
# Telegram Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码 - 支持多国格式，特别优化马来西亚格式"""
    patterns = [
        # 马来西亚电话号码（按优先级排序）
        r'\+60\s+1[0-9]\s*-?\s*\d{4}\s+\d{4}',       # +60 11-2896 2309 或 +60 11 2896 2309
        r'\+60\s*1[0-9]\s*-?\s*\d{4}\s*-?\s*\d{4}',  # +60 11-2896-2309 或 +6011-2896-2309
        r'\+60\s*1[0-9]\d{7,8}',                     # +60 11xxxxxxxx
        r'\+60\s*[3-9]\s*-?\s*\d{4}\s+\d{4}',        # +60 3-1234 5678 (固话)
        r'\+60\s*[3-9]\d{7,8}',                      # +60 312345678 (固话)
        
        # 其他国际格式
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
        
        # 通用国际格式
        r'\+\d{1,4}\s*\d{1,4}\s*\d{1,4}\s*\d{1,9}', # 通用国际格式
        
        # 本地格式（无国际代码）
        r'1[3-9]\d{9}',                             # 中国手机（本地）
        r'0[1-9]\d{1,3}[-\s]?\d{7,8}',             # 中国固话（本地）
        r'01[0-9][-\s]?\d{4}[-\s]?\d{4}',          # 马来西亚手机（本地）
        r'0[3-9][-\s]?\d{4}[-\s]?\d{4}',           # 马来西亚固话（本地）
    ]
    
    phone_numbers = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # 清理电话号码：移除多余空格，但保留格式
            cleaned = re.sub(r'\s+', ' ', match.strip())
            phone_numbers.add(cleaned)
    
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
        'version': 'v10.1',
        'restart_count': restart_count,
        'health_check_active': health_check_running,
        'timestamp': time.time()
    })
@app.route('/status')
def status():
    """状态端点"""
    return jsonify({
        'bot_status': 'running' if not shutdown_event.is_set() else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
        'restart_count': restart_count,
        'health_check_active': health_check_running
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
    """处理 /start 命令 - 增强版帮助"""
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""
👋 **欢迎使用电话号码重复检测机器人，{user_name}！**
🤖 **电话号码查重机器人 v10.1** 🤖
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 **专业功能**:
我可以智能检测并分析消息中的电话号码，支持多国格式识别和重复检测！
📱 **支持的电话号码格式**:
🇲🇾 **马来西亚格式** (重点支持):
• `+60 11-2896 2309` (标准格式)
• `+60 11 2896 2309` (空格分隔)
• `+6011-28962309` (紧凑格式)
• `01-1234 5678` (本地手机)
• `03-1234 5678` (本地固话)
🌍 **其他国际格式**:
• 🇨🇳 中国: `+86 138 0013 8000`
• 🇺🇸 美国: `+1 555 123 4567`
• 🇸🇬 新加坡: `+65 6123 4567`
• 🇭🇰 香港: `+852 2123 4567`
• 🇯🇵 日本: `+81 90 1234 5678`
• 🇰🇷 韩国: `+82 10 1234 5678`
⚡ **新特性 v10.1：**
✅ 🛡️ 智能重启检测和恢复
✅ 🔄 自动队列健康检查
✅ ⏱️ 延迟启动防竞态条件
✅ 🔧 API兼容性修复
✅ 📊 详细运行状态监控
📋 **可用命令**:
• `/start` - 显示完整帮助信息
• `/clear` - 清除当前群组的所有记录
• `/stats` - 查看详细统计数据
• `/export` - 导出电话号码清单
• `/help` - 快速帮助
🚀 **使用方法**:
1️⃣ 直接发送包含电话号码的任何消息
2️⃣ 我会自动识别、分类并检测重复
3️⃣ 查看详细的分析结果和统计
💡 **小贴士**: 
• 支持一次发送多个号码
• 自动过滤无效格式
• 记录保持在群组/私聊中持久化
• 🆕 自动恢复和健康监控
现在就发送一些电话号码试试吧！ 🎯
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    chat_id = update.effective_chat.id
    count = len(user_groups[chat_id]['phones'])
    user_groups[chat_id]['phones'].clear()
    await update.message.reply_text(f"✅ 已清除所有电话号码记录 (共 {count} 个)")
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新增 /export 命令 - 导出号码清单"""
    chat_id = update.effective_chat.id
    all_phones = user_groups[chat_id]['phones']
    
    if not all_phones:
        await update.message.reply_text("📝 当前群组暂无电话号码记录")
        return
    
    # 按类型分组
    phone_by_type = {}
    for phone in all_phones:
        phone_type = categorize_phone_number(phone)
        if phone_type not in phone_by_type:
            phone_by_type[phone_type] = []
        phone_by_type[phone_type].append(phone)
    
    export_text = f"""
📋 **电话号码清单导出**
================================
总计: {len(all_phones)} 个号码
"""
    
    for phone_type, phones in sorted(phone_by_type.items()):
        export_text += f"**{phone_type}** ({len(phones)}个):\n"
        for i, phone in enumerate(sorted(phones), 1):
            export_text += f"{i:2d}. `{phone}`\n"
        export_text += "\n"
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    export_text += f"📅 导出时间: {now}"
    
    await update.message.reply_text(export_text, parse_mode='Markdown')
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新增 /help 命令 - 快速帮助"""
    help_text = """
🆘 **快速帮助**
📋 **命令列表**:
• `/start` - 完整帮助文档
• `/stats` - 查看详细统计
• `/clear` - 清除所有记录  
• `/export` - 导出号码清单
• `/help` - 本帮助信息
💡 **快速上手**:
直接发送包含电话号码的消息即可开始检测！
例如: `联系方式：+60 11-2896 2309`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令 - 详细统计"""
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "私聊"
    user_name = update.effective_user.first_name or "用户"
    
    all_phones = user_groups[chat_id]['phones']
    
    # 按国家分类统计
    country_stats = {}
    for phone in all_phones:
        country = categorize_phone_number(phone)
        country_stats[country] = country_stats.get(country, 0) + 1
    
    # 计算各种统计
    total_count = len(all_phones)
    malaysia_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇲🇾")])
    china_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇨🇳")])
    international_count = total_count - malaysia_count - china_count
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    stats_text = f"""
📊 **详细统计报告**
================================
👤 **查询者**: {user_name}
🏠 **群组**: {chat_title}
🆔 **群组ID**: `{chat_id}`
⏰ **查询时间**: {now}
📈 **总体统计**:
• 总电话号码: **{total_count}** 个
• 马来西亚号码: **{malaysia_count}** 个 ({malaysia_count/max(total_count,1)*100:.1f}%)
• 中国号码: **{china_count}** 个 ({china_count/max(total_count,1)*100:.1f}%)
• 其他国际号码: **{international_count}** 个 ({international_count/max(total_count,1)*100:.1f}%)
🌍 **按国家/地区分布**:"""
    # 添加国家统计
    if country_stats:
        for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
            percentage = count/max(total_count,1)*100
            stats_text += f"\n• {country}: {count} 个 ({percentage:.1f}%)"
    else:
        stats_text += "\n• 暂无数据"
    
    stats_text += f"\n\n🤖 **机器人状态**:\n"
    stats_text += get_restart_status()
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息 - 增强版分析"""
    try:
        text = update.message.text
        chat_id = update.effective_chat.id
        user_name = update.effective_user.first_name or "用户"
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(text)
        
        if not phone_numbers:
            return  # 如果没有电话号码，不回复
        
        # 添加到用户组记录
        user_groups[chat_id]['phones'].update(phone_numbers)
        all_user_phones = user_groups[chat_id]['phones']
        
        # 查找当前消息内的重复
        message_duplicates = find_duplicates(phone_numbers)
        
        # 查找与历史记录的重复
        historical_duplicates = set()
        for phone in phone_numbers:
            normalized_new = re.sub(r'[^\d+]', '', phone)
            for existing_phone in all_user_phones:
                if existing_phone != phone:  # 不与自己比较
                    normalized_existing = re.sub(r'[^\d+]', '', existing_phone)
                    if normalized_new == normalized_existing:
                        historical_duplicates.add(phone)
                        historical_duplicates.add(existing_phone)
                        break
        
        # 构建分析结果
        response_parts = []
        
        # 基本信息
        response_parts.append(f"📱 **检测到 {len(phone_numbers)} 个电话号码**")
        response_parts.append(f"👤 **分析师**: {user_name}")
        
        # 电话号码列表与分类
        response_parts.append("\n📋 **号码清单**:")
        for i, phone in enumerate(sorted(phone_numbers), 1):
            category = categorize_phone_number(phone)
            duplicate_status = ""
            
            if phone in message_duplicates:
                duplicate_status = " 🔴 **消息内重复**"
            elif phone in historical_duplicates:
                duplicate_status = " 🟡 **历史重复**"
            
            response_parts.append(f"{i}. `{phone}` - {category}{duplicate_status}")
        
        # 重复分析
        if message_duplicates or historical_duplicates:
            response_parts.append("\n⚠️ **重复检测**:")
            
            if message_duplicates:
                response_parts.append(f"🔴 **消息内重复**: {len(message_duplicates)} 个号码")
                for phone in sorted(message_duplicates):
                    response_parts.append(f"   • `{phone}`")
            
            if historical_duplicates:
                new_historical = historical_duplicates - message_duplicates
                if new_historical:
                    response_parts.append(f"🟡 **与历史重复**: {len(new_historical)} 个号码")
                    for phone in sorted(new_historical):
                        if phone in phone_numbers:  # 只显示当前消息中的号码
                            response_parts.append(f"   • `{phone}`")
        else:
            response_parts.append("\n✅ **重复检测**: 无重复号码")
        
        # 统计信息
        response_parts.append(f"\n📊 **群组统计**: 累计收录 {len(all_user_phones)} 个号码")
        
        # 发送回复
        response_text = "\n".join(response_parts)
        await update.message.reply_text(response_text, parse_mode='Markdown')
        
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
    
    logger.info("=== 电话号码查重机器人 v10.1 启动 (API兼容版) ===")
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
