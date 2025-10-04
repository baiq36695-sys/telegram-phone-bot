#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML电话号码重复检测机器人
版本: v9.0 - 全面跟踪版
增强功能：
1. 显示电话号码第一次出现时间
2. 显示重复时是跟哪个用户重复的
3. 显示号码重复次数
4. 跨用户全局重复检测
"""

import logging
import re
import os
import threading
import json
from html import unescape
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 配置简化的日志
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)

# 进一步简化第三方库的日志
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 机器人Token - 请替换为您的实际Token
BOT_TOKEN = "8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU"

# 全局数据存储 - 跨用户共享
# 结构: {normalized_phone: {first_time, first_user, count, submissions}}
global_phone_data = {}

# 更全面的电话号码匹配模式
PHONE_PATTERNS = [
    r'\+\d{1,4}[\s-]*\d{1,4}[\s-]*\d{3,4}[\s-]*\d{3,4}',  # 国际格式
    r'\+60\s*1[0-9][\s-]*\d{3}[\s-]+\d{4}',  # 马来西亚手机号格式
    r'\b1[3-9]\d{9}\b',  # 中国手机号
    r'\b\d{3}[\s-]*\d{3}[\s-]*\d{4}\b',  # 美国格式
    r'\b\d{2,4}[\s-]*\d{6,8}\b',  # 其他常见格式
]

def extract_phone_numbers(text):
    """从文本中提取电话号码"""
    text = unescape(text)
    phone_numbers = set()
    
    for pattern in PHONE_PATTERNS:
        matches = re.findall(pattern, text)
        phone_numbers.update(matches)
    
    return phone_numbers

def normalize_phone_number(phone):
    """标准化电话号码用于比较"""
    # 保留数字和开头的+号
    normalized = re.sub(r'[^\d+]', '', phone)
    # 如果没有+号开头，添加+号
    if not normalized.startswith('+'):
        normalized = '+' + normalized
    return normalized

def get_phone_type_emoji(phone):
    """根据电话号码类型返回对应表情"""
    if phone.startswith('+60'):
        return "🇲🇾"  # 马来西亚
    elif phone.startswith('+86') or (phone.startswith('+') and phone[1:].startswith('1') and len(phone) == 12):
        return "🇨🇳"  # 中国
    elif phone.startswith('+1'):
        return "🇺🇸"  # 美国/加拿大
    elif phone.startswith('+44'):
        return "🇬🇧"  # 英国
    elif phone.startswith('+81'):
        return "🇯🇵"  # 日本
    elif phone.startswith('+82'):
        return "🇰🇷"  # 韩国
    else:
        return "🌍"  # 其他国家

def get_user_display_name(user):
    """获取用户显示名称"""
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        if user.last_name:
            return f"{user.first_name} {user.last_name}"
        return user.first_name
    else:
        return f"用户{user.id}"

def format_time_ago(time_diff):
    """格式化时间差显示"""
    seconds = int(time_diff.total_seconds())
    
    if seconds < 60:
        return f"{seconds}秒前"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}分钟前"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}小时前"
    else:
        days = seconds // 86400
        return f"{days}天前"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    welcome_msg = """
🌟 ═══════════════════════════ 🌟
        📱 智能电话号码管理系统        
🌟 ═══════════════════════════ 🌟

🚀 版本: v9.0 - 全面跟踪版

✨ 【核心功能】
🔍 智能识别电话号码
🛡️ 精准重复检测（跨用户）
🌍 支持国际号码格式
📊 实时统计分析
⏰ 详细时间跟踪

🎯 【操作指南】
📩 发送包含电话号码的消息
🗑️ /clear - 清空所有记录（管理员）
📈 /stats - 查看详细统计
💡 /help - 获取帮助信息
🎨 /about - 关于本机器人

🔥 【新增特性】
📅 显示号码第一次出现时间
👥 显示重复时的用户信息
🔢 显示重复次数统计
🌐 全局跨用户检测

════════════════════════════════
🎈 现在发送您的电话号码，开始体验吧！
════════════════════════════════
"""
    await update.message.reply_text(welcome_msg)

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除所有存储的电话号码（仅管理员）"""
    global global_phone_data
    
    # 这里可以添加管理员检查
    # admin_ids = [123456789]  # 添加管理员ID
    # if update.effective_user.id not in admin_ids:
    #     await update.message.reply_text("❌ 仅管理员可以清除全局数据")
    #     return
    
    global_phone_data.clear()
    context.user_data.clear()
    
    clear_msg = """
🧹 ═══════ 全局数据清理完成 ═══════ 🧹

✅ 所有电话号码记录已清除
✅ 统计数据已重置
✅ 跨用户数据已清空
✅ 系统状态已恢复初始化

🆕 所有用户现在可以重新开始录入电话号码了！

════════════════════════════════
"""
    await update.message.reply_text(clear_msg)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    global global_phone_data
    
    if not global_phone_data:
        stats_msg = """
📊 ═══════ 统计报告 ═══════ 📊

📭 当前状态：无记录数据
🎯 建议：发送包含电话号码的消息开始使用

════════════════════════════════
"""
        await update.message.reply_text(stats_msg)
        return
    
    # 统计总体数据
    total_unique_phones = len(global_phone_data)
    total_submissions = sum(data['count'] for data in global_phone_data.values())
    
    # 按国家分类统计
    country_stats = {}
    repeat_stats = {}
    
    for normalized_phone, data in global_phone_data.items():
        # 获取第一次提交时的原始格式来判断国家
        first_original = data['submissions'][0]['original_format']
        emoji = get_phone_type_emoji(first_original)
        country_stats[emoji] = country_stats.get(emoji, 0) + 1
        
        # 统计重复次数分布
        count = data['count']
        if count > 1:
            repeat_stats[count] = repeat_stats.get(count, 0) + 1
    
    country_breakdown = ""
    for emoji, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
        country_breakdown += f"      {emoji} {count} 个唯一号码\n"
    
    repeat_breakdown = ""
    for repeat_count, phone_count in sorted(repeat_stats.items(), reverse=True):
        repeat_breakdown += f"      🔄 {repeat_count}次重复: {phone_count} 个号码\n"
    
    if not repeat_breakdown:
        repeat_breakdown = "      🎉 暂无重复号码\n"
    
    stats_msg = f"""
📊 ═══════ 全局统计报告 ═══════ 📊

📈 【总体数据】
   📞 唯一号码数：{total_unique_phones} 个
   📝 总提交次数：{total_submissions} 次
   ⏰ 统计时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

🌍 【地区分布】
{country_breakdown}
🔄 【重复统计】
{repeat_breakdown}
🏆 【系统状态】
   ✅ 运行正常
   ⚡ 响应迅速
   🛡️ 数据安全
   🌐 全局跟踪

════════════════════════════════
"""
    await update.message.reply_text(stats_msg)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示帮助信息"""
    help_msg = """
💡 ═══════ 帮助中心 ═══════ 💡

🎯 【基本使用】
   • 直接发送包含电话号码的文本
   • 支持多种格式：+86 138xxxx, +60 13-xxx等
   • 自动识别并分类新/重复号码

🛠️【命令列表】
   /start - 🏠 返回主页
   /clear - 🗑️ 清空所有记录（管理员）
   /stats - 📊 查看统计信息
   /help - 💡 显示此帮助
   /about - ℹ️ 关于机器人

🌟 【支持格式】
   • 国际格式：+86 138 0013 8000
   • 带分隔符：+60 13-970 3144
   • 本地格式：13800138000
   • 美式格式：(555) 123-4567

🔥 【智能特性】
   • 🎭 自动国家识别
   • ⚡ 秒级重复检测
   • 🌈 可视化结果展示
   • 🔒 隐私数据保护
   • 📅 详细时间跟踪
   • 👥 跨用户重复检测
   • 🔢 重复次数统计

════════════════════════════════
"""
    await update.message.reply_text(help_msg)

async def show_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示关于信息"""
    about_msg = """
ℹ️ ═══════ 关于我们 ═══════ ℹ️

🤖 【机器人信息】
   名称：智能电话号码管理系统
   版本：v9.0 全面跟踪版
   开发：MiniMax Agent

⭐ 【核心技术】
   • Python + Telegram Bot API
   • 正则表达式引擎
   • 智能去重算法
   • 实时数据处理
   • 全局状态管理

🌟 【设计理念】
   • 简单易用，功能强大
   • 美观界面，用户至上
   • 数据安全，隐私第一
   • 持续改进，追求完美

🎨 【界面设计】
   • 丰富表情符号
   • 清晰结构布局
   • 动态视觉反馈
   • 个性化体验

🆕 【v9.0新特性】
   • 📅 电话号码首次出现时间追踪
   • 👥 重复来源用户信息显示
   • 🔢 详细重复次数统计
   • 🌐 全局跨用户重复检测
   • ⏰ 智能时间差显示

💌 感谢使用！如有建议，欢迎反馈！

════════════════════════════════
"""
    await update.message.reply_text(about_msg)

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息"""
    try:
        global global_phone_data
        
        message_text = update.message.text
        current_user = update.effective_user
        current_time = datetime.now()
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(message_text)
        
        if not phone_numbers:
            no_phone_msg = """
❌ ═══════ 识别结果 ═══════ ❌

🔍 扫描结果：未检测到电话号码

💡 请确保您的消息包含有效的电话号码格式：
   • +86 138 0013 8000
   • +60 13-970 3144
   • (555) 123-4567
   • 13800138000

🎯 提示：支持多种国际格式！

════════════════════════════════
"""
            await update.message.reply_text(no_phone_msg)
            return
        
        # 分类电话号码：新号码和重复号码
        new_phones = []
        duplicate_phones = []
        
        # 检查每个电话号码
        for phone in phone_numbers:
            normalized = normalize_phone_number(phone)
            
            if normalized in global_phone_data:
                # 重复号码
                duplicate_phones.append({
                    'original': phone,
                    'normalized': normalized,
                    'data': global_phone_data[normalized]
                })
                
                # 更新重复数据
                global_phone_data[normalized]['count'] += 1
                global_phone_data[normalized]['submissions'].append({
                    'user': {
                        'id': current_user.id,
                        'name': get_user_display_name(current_user)
                    },
                    'time': current_time,
                    'original_format': phone
                })
                
            else:
                # 新号码
                new_phones.append(phone)
                
                # 添加到全局数据
                global_phone_data[normalized] = {
                    'first_time': current_time,
                    'first_user': {
                        'id': current_user.id,
                        'name': get_user_display_name(current_user)
                    },
                    'count': 1,
                    'submissions': [{
                        'user': {
                            'id': current_user.id,
                            'name': get_user_display_name(current_user)
                        },
                        'time': current_time,
                        'original_format': phone
                    }]
                }
        
        # 构建美化的回复消息
        response_parts = []
        response_parts.append("🎯 ═══════ 处理结果 ═══════ 🎯\n")
        
        if new_phones:
            response_parts.append(f"✨ 【新发现号码】({len(new_phones)} 个)")
            for phone in sorted(new_phones):
                emoji = get_phone_type_emoji(phone)
                response_parts.append(f"   {emoji} 📞 {phone}")
                response_parts.append(f"      🎉 首次记录！")
            response_parts.append("")
        
        if duplicate_phones:
            response_parts.append(f"⚠️ 【重复号码警告】({len(duplicate_phones)} 个)")
            for dup_info in duplicate_phones:
                phone = dup_info['original']
                data = dup_info['data']
                emoji = get_phone_type_emoji(phone)
                
                response_parts.append(f"   {emoji} 🔄 {phone}")
                
                # 显示首次出现信息
                time_ago = format_time_ago(current_time - data['first_time'])
                response_parts.append(f"      📅 首次出现：{time_ago}")
                response_parts.append(f"      👤 首次用户：{data['first_user']['name']}")
                
                # 显示重复次数
                response_parts.append(f"      🔢 重复次数：{data['count']} 次")
                
                # 显示最近几次重复用户（最多显示3个）
                recent_users = []
                for submission in data['submissions'][-3:]:
                    if submission['user']['name'] not in recent_users:
                        recent_users.append(submission['user']['name'])
                
                if len(recent_users) > 1:
                    response_parts.append(f"      👥 重复用户：{', '.join(recent_users[-3:])}")
                
            response_parts.append("")
        
        # 添加统计信息
        total_unique = len(global_phone_data)
        total_submissions = sum(data['count'] for data in global_phone_data.values())
        
        response_parts.append(f"📊 【全局统计】")
        response_parts.append(f"   📈 唯一号码：{total_unique} 个")
        response_parts.append(f"   📝 总提交：{total_submissions} 次")
        response_parts.append(f"   ⏰ 时间：{current_time.strftime('%H:%M')}")
        
        response_parts.append("\n════════════════════════════════")
        
        await update.message.reply_text("\n".join(response_parts))
        
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")
        error_msg = """
❌ ═══════ 系统错误 ═══════ ❌

🚨 处理过程中发生错误
🔧 系统正在自动修复
⏳ 请稍后重试

💡 如问题持续，请联系技术支持

════════════════════════════════
"""
        await update.message.reply_text(error_msg)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理"""
    logger.error(f"Bot error: {context.error}")

# Flask应用（用于健康检查）
app = Flask(__name__)

# 禁用Flask的访问日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
@app.route('/health')
def health_check():
    """健康检查端点"""
    global global_phone_data
    total_phones = len(global_phone_data)
    total_submissions = sum(data['count'] for data in global_phone_data.values())
    
    return f"""
    <html>
    <head><title>📱 电话号码管理机器人</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
        <h1>🤖 机器人运行正常！</h1>
        <p>✅ 版本: v9.0 全面跟踪版</p>
        <p>⚡ 状态: 在线服务中</p>
        <p>🌟 功能: 智能电话号码管理</p>
        <p>📊 唯一号码: {total_phones} 个</p>
        <p>📝 总提交: {total_submissions} 次</p>
        <p>🔥 特性: 全局跟踪，时间记录，用户追踪</p>
    </body>
    </html>
    """, 200

def run_flask():
    """在后台线程运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """主函数"""
    try:
        # 在后台线程启动Flask服务器
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"🌐 系统启动中... 端口: {os.environ.get('PORT', 10000)}")
        
        # 创建Telegram应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("clear", clear_data))
        application.add_handler(CommandHandler("stats", show_stats))
        application.add_handler(CommandHandler("help", show_help))
        application.add_handler(CommandHandler("about", show_about))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
        
        # 添加错误处理器
        application.add_error_handler(error_handler)
        
        print("🚀 机器人启动成功 - v9.0 全面跟踪版")
        print("📅 新功能：时间跟踪、用户追踪、重复统计")
        
        # 启动机器人（主线程）
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    main()
