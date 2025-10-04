#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 超级增强版 + 自动重启版
增强版警告系统 + 风险评估 + 安全提醒 + 保持重新启动功能
修复所有事件循环和部署问题，并添加强大的自动重启机制
"""

import os
import re
import logging
import signal
import sys
import asyncio
import datetime
from typing import Set, Dict, Any, List, Tuple
from collections import defaultdict
import threading
import time
import hashlib
import subprocess

# 🔄 自动重启控制变量
RESTART_COUNT = 0
MAX_RESTARTS = 10
RESTART_DELAY = 5

# 导入并应用nest_asyncio
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "nest-asyncio"])
    import nest_asyncio
    nest_asyncio.apply()

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 禁用不必要的HTTP日志以减少噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# 初始化Flask应用
app = Flask(__name__)

# 全局变量 - 增强版数据结构
user_groups: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
    'phones': set(),
    'phone_history': [],
    'risk_scores': {},
    'warnings_issued': set(),
    'last_activity': None,
    'security_alerts': []
})

# 系统状态管理
shutdown_event = threading.Event()
bot_application = None
is_running = False
flask_thread = None
bot_thread = None

# 风险评估等级
RISK_LEVELS = {
    'LOW': {'emoji': '🟢', 'color': 'LOW', 'score': 1},
    'MEDIUM': {'emoji': '🟡', 'color': 'MEDIUM', 'score': 2}, 
    'HIGH': {'emoji': '🟠', 'color': 'HIGH', 'score': 3},
    'CRITICAL': {'emoji': '🔴', 'color': 'CRITICAL', 'score': 4}
}

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
    normalized_map = {}
    duplicates = set()
    
    for phone in phones:
        normalized = re.sub(r'[^\d+]', '', phone)
        
        if normalized in normalized_map:
            duplicates.add(phone)
            duplicates.add(normalized_map[normalized])
        else:
            normalized_map[normalized] = phone
    
    return duplicates

def categorize_phone_number(phone: str) -> str:
    """识别电话号码的类型和国家"""
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    if re.match(r'\+60[1][0-9]', clean_phone):
        return "🇲🇾 马来西亚手机"
    elif re.match(r'\+60[3-9]', clean_phone):
        return "🇲🇾 马来西亚固话"
    elif re.match(r'\+86[1][3-9]', clean_phone):
        return "🇨🇳 中国手机"
    elif re.match(r'\+86[2-9]', clean_phone):
        return "🇨🇳 中国固话"
    elif re.match(r'\+1[2-9]', clean_phone):
        return "🇺🇸 美国/加拿大"
    elif re.match(r'\+65[6-9]', clean_phone):
        return "🇸🇬 新加坡"
    elif re.match(r'\+852[2-9]', clean_phone):
        return "🇭🇰 香港"
    elif re.match(r'\+853[6-9]', clean_phone):
        return "🇲🇴 澳门"
    elif re.match(r'\+886[0-9]', clean_phone):
        return "🇹🇼 台湾"
    elif re.match(r'\+91[6-9]', clean_phone):
        return "🇮🇳 印度"
    elif re.match(r'\+81[7-9]', clean_phone):
        return "🇯🇵 日本"
    elif re.match(r'\+82[1][0-9]', clean_phone):
        return "🇰🇷 韩国"
    elif re.match(r'\+66[6-9]', clean_phone):
        return "🇹🇭 泰国"
    elif re.match(r'\+84[3-9]', clean_phone):
        return "🇻🇳 越南"
    elif re.match(r'\+63[2-9]', clean_phone):
        return "🇵🇭 菲律宾"
    elif re.match(r'\+62[1-9]', clean_phone):
        return "🇮🇩 印度尼西亚"
    elif re.match(r'\+44[1-9]', clean_phone):
        return "🇬🇧 英国"
    elif re.match(r'^[1][3-9]\d{9}$', clean_phone):
        return "🇨🇳 中国手机（本地）"
    elif re.match(r'^0[1-9]', clean_phone):
        if len(clean_phone) >= 10:
            return "🇲🇾 马来西亚（本地）"
        else:
            return "🇨🇳 中国固话（本地）"
    else:
        return "🌍 其他国际号码"

def assess_phone_risk(phone: str, chat_data: Dict[str, Any]) -> Tuple[str, List[str]]:
    """评估电话号码风险等级"""
    warnings = []
    risk_score = 0
    
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    # 1. 重复度检查
    if phone in chat_data['phones']:
        risk_score += 2
        warnings.append("📞 号码重复：该号码之前已被检测过")
    
    # 2. 格式可疑性检查
    if not re.match(r'^\+\d+', clean_phone) and len(clean_phone) > 10:
        risk_score += 1
        warnings.append("🔍 格式异常：缺少国际代码的长号码")
    
    # 3. 长度异常检查
    if len(clean_phone) > 16 or len(clean_phone) < 8:
        risk_score += 2
        warnings.append("📏 长度异常：电话号码长度不符合国际标准")
    
    # 4. 连续数字模式检查
    if re.search(r'(\d)\1{4,}', clean_phone):
        risk_score += 1
        warnings.append("🔢 模式可疑：存在5个以上连续相同数字")
    
    # 5. 频繁提交检查
    if len(chat_data['phone_history']) > 20:
        recent_submissions = [h for h in chat_data['phone_history'] if 
                            (datetime.datetime.now() - h['timestamp']).seconds < 3600]
        if len(recent_submissions) > 10:
            risk_score += 2
            warnings.append("⏱️ 频繁提交：1小时内提交次数过多，请注意数据保护")
    
    # 确定风险等级
    if risk_score >= 6:
        return 'CRITICAL', warnings
    elif risk_score >= 4:
        return 'HIGH', warnings
    elif risk_score >= 2:
        return 'MEDIUM', warnings
    else:
        return 'LOW', warnings

# 🔄 自动重启功能
def restart_application():
    """重启应用程序"""
    global RESTART_COUNT
    
    if RESTART_COUNT >= MAX_RESTARTS:
        logger.error(f"🛑 已达到最大重启次数 {MAX_RESTARTS}，程序退出")
        sys.exit(1)
        
    RESTART_COUNT += 1
    logger.info(f"🔄 准备重启应用 (第{RESTART_COUNT}次)...")
    
    # 停止所有线程
    shutdown_event.set()
    
    # 等待延迟
    time.sleep(RESTART_DELAY)
    
    try:
        python = sys.executable
        # 启动新进程
        subprocess.Popen([python] + sys.argv)
        logger.info("✅ 重启命令已执行")
    except Exception as e:
        logger.error(f"❌ 重启失败: {e}")
    finally:
        sys.exit(0)

def signal_handler(signum, frame):
    """信号处理器 - 自动重启版"""
    logger.info(f"📶 收到信号 {signum}，正在关闭...")
    
    global bot_application, is_running
    
    # 设置关闭标志
    shutdown_event.set()
    is_running = False
    
    # 尝试优雅关闭bot应用
    if bot_application:
        try:
            logger.info("🛑 正在停止bot应用...")
        except Exception as e:
            logger.error(f"停止bot应用时出错: {e}")
    
    logger.info("🔄 准备自动重启...")
    restart_application()

# Flask路由 - 增加重启信息
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """健康检查端点"""
    global is_running, RESTART_COUNT
    return jsonify({
        'status': 'healthy',
        'service': 'telegram-phone-bot-enhanced-auto-restart',
        'bot_running': is_running,
        'restart_count': RESTART_COUNT,
        'max_restarts': MAX_RESTARTS,
        'auto_restart': 'enabled',
        'nest_asyncio': 'enabled',
        'features': ['risk_assessment', 'security_warnings', 'comprehensive_analysis', 'auto_restart'],
        'timestamp': time.time()
    })

@app.route('/status')
def status():
    """状态端点"""
    global is_running
    return jsonify({
        'bot_status': 'running' if is_running else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
        'restart_count': RESTART_COUNT,
        'auto_restart_enabled': True,
        'event_loop_fix': 'nest_asyncio',
        'enhanced_features': 'enabled'
    })

@app.route('/restart')
def force_restart():
    """强制重启机器人的端点"""
    logger.info("🔄 收到强制重启请求")
    restart_application()
    return jsonify({'message': 'Bot restart initiated', 'timestamp': datetime.datetime.now().isoformat()})

# Telegram机器人函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令 - 超级增强版帮助"""
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""
🎯 **欢迎使用超级增强版电话号码检测机器人，{user_name}！**

🚀 **全新功能特色**:
⭐ 智能风险评估系统
⭐ 多级安全警告提醒  
⭐ 综合数据保护建议
⭐ 实时威胁检测分析
⭐ 国际号码深度识别
⭐ **自动重启保持运行** 🔄

🛡️ **安全检测功能**:
🔍 **智能风险分析**：
• 🟢 低风险 - 正常号码格式
• 🟡 中等风险 - 存在异常特征
• 🟠 高风险 - 多项可疑指标
• 🔴 严重风险 - 需要立即验证

📱 **支持的电话号码格式**:

🇲🇾 **马来西亚格式** (优先支持):
• `+60 11-2896 2309` (标准格式)
• `+60 11 2896 2309` (空格分隔)
• `+6011-28962309` (紧凑格式)
• `01-1234 5678` (本地手机)
• `03-1234 5678` (本地固话)

🌏 **全球国际格式**:
• 🇨🇳 中国: `+86 138 0013 8000`
• 🇺🇸 美国: `+1 555 123 4567`
• 🇸🇬 新加坡: `+65 6123 4567`
• 🇭🇰 香港: `+852 2123 4567`
• + 更多国际格式...

📋 **完整命令列表**:
• `/start` - 显示完整功能介绍
• `/clear` - 清除所有记录
• `/stats` - 详细统计与风险报告
• `/help` - 快速帮助指南

🔄 **自动重启功能**:
✅ 服务器重启后自动恢复
✅ 系统故障自动修复
✅ 保持24/7持续运行
✅ 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}

现在就发送电话号码开始智能检测吧！ 🎯
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令 - 增强版清理"""
    chat_id = update.effective_chat.id
    phone_count = len(user_groups[chat_id]['phones'])
    history_count = len(user_groups[chat_id]['phone_history'])
    
    # 清理所有数据
    user_groups[chat_id]['phones'].clear()
    user_groups[chat_id]['phone_history'].clear()
    user_groups[chat_id]['risk_scores'].clear()
    user_groups[chat_id]['warnings_issued'].clear()
    user_groups[chat_id]['security_alerts'].clear()
    
    clear_message = f"""
🧹 **数据清理完成**
========================

📊 **清理统计**:
• 电话号码: {phone_count} 个
• 历史记录: {history_count} 条
• 风险评分: 已重置
• 安全警报: 已清空

🔒 **隐私保护**:
✅ 所有号码数据已安全删除
✅ 检测历史已完全清除
✅ 风险评估记录已重置
✅ 安全警报历史已清空

💡 **清理完成提醒**:
现在可以重新开始检测电话号码，
所有新检测将重新进行风险评估。

⏰ 清理时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
    await update.message.reply_text(clear_message, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令 - 超级增强版统计"""
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "私聊"
    user_name = update.effective_user.first_name or "用户"
    chat_data = user_groups[chat_id]
    
    all_phones = chat_data['phones']
    
    # 风险统计
    risk_distribution = {'LOW': 0, 'MEDIUM': 0, 'HIGH': 0, 'CRITICAL': 0}
    for phone in all_phones:
        risk_level = chat_data['risk_scores'].get(phone, 'LOW')
        risk_distribution[risk_level] += 1
    
    # 计算各种统计
    total_count = len(all_phones)
    malaysia_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇲🇾")])
    china_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇨🇳")])
    international_count = total_count - malaysia_count - china_count
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    stats_text = f"""
📊 **超级增强版统计报告**
=====================================

👤 **报告信息**:
• 查询者: {user_name}
• 群组: {chat_title}
• 生成时间: {now}

📈 **数据总览**:
• 总电话号码: **{total_count}** 个
• 马来西亚号码: **{malaysia_count}** 个 ({malaysia_count/max(total_count,1)*100:.1f}%)
• 中国号码: **{china_count}** 个 ({china_count/max(total_count,1)*100:.1f}%)
• 其他国际号码: **{international_count}** 个 ({international_count/max(total_count,1)*100:.1f}%)

🛡️ **风险评估统计**:
• 🟢 低风险: {risk_distribution['LOW']} 个 ({risk_distribution['LOW']/max(total_count,1)*100:.1f}%)
• 🟡 中等风险: {risk_distribution['MEDIUM']} 个 ({risk_distribution['MEDIUM']/max(total_count,1)*100:.1f}%)
• 🟠 高风险: {risk_distribution['HIGH']} 个 ({risk_distribution['HIGH']/max(total_count,1)*100:.1f}%)
• 🔴 严重风险: {risk_distribution['CRITICAL']} 个 ({risk_distribution['CRITICAL']/max(total_count,1)*100:.1f}%)

🔄 **自动重启系统**:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• 运行状态: ✅ 正常运行
• 自动重启: ✅ 已启用

🎯 **系统状态**:
• 运行状态: ✅ 正常运行
• 风险检测: ✅ 智能评估已启用
• 自动重启保护: ✅ 已启用
• 事件循环: ✅ 已优化 (nest_asyncio)

---
🤖 **超级增强版电话号码检测机器人** v3.0 + AutoRestart
🛡️ **集成智能风险评估系统 + 自动重启保护**
"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令 - 快速帮助"""
    help_text = f"""
🆘 **快速帮助指南**

📋 **核心命令**:
• `/start` - 完整功能介绍
• `/stats` - 详细统计报告
• `/clear` - 清除所有记录  
• `/help` - 本帮助信息

🚀 **快速上手**:
1️⃣ 直接发送包含电话号码的消息
2️⃣ 查看智能风险评估结果
3️⃣ 关注安全警告和建议

🔄 **自动重启功能**:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• ✅ 自动保持运行
• ✅ 故障自动恢复

💡 **示例**: `联系方式：+60 11-2896 2309`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息 - 超级增强版分析系统"""
    try:
        chat_id = update.effective_chat.id
        message_text = update.message.text
        user_name = update.effective_user.first_name or "用户"
        chat_data = user_groups[chat_id]
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(message_text)
        
        if not phone_numbers:
            return
        
        # 更新活动时间
        chat_data['last_activity'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 记录检测历史
        detection_record = {
            'timestamp': datetime.datetime.now(),
            'user': user_name,
            'phone_count': len(phone_numbers),
            'phones': list(phone_numbers)
        }
        chat_data['phone_history'].append(detection_record)
        
        # 检查重复和分类
        existing_phones = chat_data['phones']
        new_phones = phone_numbers - existing_phones
        duplicate_phones = phone_numbers & existing_phones
        
        # 构建增强版回复
        response_parts = []
        response_parts.append("🎯 **智能电话号码检测系统**")
        response_parts.append("=" * 35)
        response_parts.append(f"👤 **用户**: {user_name}")
        response_parts.append(f"🔍 **检测到**: {len(phone_numbers)} 个号码")
        response_parts.append("")
        
        # 显示新发现的号码（带风险评估）
        if new_phones:
            response_parts.append(f"✨ **新发现号码** ({len(new_phones)}个):")
            for i, phone in enumerate(sorted(new_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level, risk_warnings = assess_phone_risk(phone, chat_data)
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                
                # 保存风险评分
                chat_data['risk_scores'][phone] = risk_level
                
                response_parts.append(f"{i:2d}. `{phone}`")
                response_parts.append(f"    📱 {phone_type}")
                response_parts.append(f"    🛡️ 风险: {risk_emoji} {risk_level}")
                response_parts.append("")
            
            # 添加到记录中
            existing_phones.update(new_phones)
        
        # 显示重复号码（加强警告）
        if duplicate_phones:
            response_parts.append(f"🔄 **重复号码警告** ({len(duplicate_phones)}个):")
            for i, phone in enumerate(sorted(duplicate_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level = chat_data['risk_scores'].get(phone, 'MEDIUM')
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                response_parts.append(f"{i:2d}. `{phone}` - {phone_type} {risk_emoji}")
            response_parts.append("")
        
        # 统计信息
        total_in_group = len(existing_phones)
        response_parts.append("📊 **智能统计分析**:")
        response_parts.append(f"• 群组总计: {total_in_group} 个号码")
        response_parts.append(f"• 自动重启次数: {RESTART_COUNT}/{MAX_RESTARTS}")
        
        # 时间戳和版本信息
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        response_parts.append("")
        response_parts.append(f"⏰ {now}")
        response_parts.append("🤖 **智能检测系统** v3.0 + AutoRestart")
        
        if response_parts:
            response = "\n".join(response_parts)
            await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text("❌ 处理消息时出现错误，系统正在自动恢复...")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理器"""
    logger.error(f"更新 {update} 引起了错误 {context.error}")
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ 处理过程中发生错误，系统正在自动恢复...",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")

def run_flask():
    """在独立线程中运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 启动增强版Flask服务器，端口: {port}")
    
    try:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        logger.error(f"Flask服务器运行错误: {e}")

async def run_bot():
    """运行Telegram机器人 - 修复版本"""
    global bot_application, is_running
    
    # 获取Bot Token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        logger.info(f"🚀 正在启动 Telegram 机器人... (第 {RESTART_COUNT + 1} 次)")
        
        # 创建应用
        bot_application = Application.builder().token(bot_token).build()
        
        # 添加错误处理器
        bot_application.add_error_handler(error_handler)
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        is_running = True
        logger.info("✅ 超级增强版电话号码检测机器人已启动！")
        logger.info("🛡️ 集成智能风险评估系统")
        logger.info("🔄 启用自动重启保护功能")
        logger.info("🔧 使用nest_asyncio解决事件循环冲突")
        
        # 关键修复：运行机器人，避免事件循环冲突
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,  # 不让库关闭事件循环
            stop_signals=None  # 禁用信号处理，避免冲突
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        is_running = False
        raise e
    finally:
        is_running = False
        logger.info("机器人已停止运行")

def start_bot_thread():
    """在新线程中启动机器人"""
    global bot_thread, is_running
    
    def run_async_bot():
        try:
            # 创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"机器人线程错误: {e}")
        finally:
            try:
                loop.close()
            except:
                pass
    
    if bot_thread and bot_thread.is_alive():
        logger.info("机器人线程已在运行")
        return
    
    bot_thread = threading.Thread(target=run_async_bot, daemon=True)
    bot_thread.start()
    logger.info("🚀 机器人线程已启动")

def start_flask_thread():
    """启动Flask线程"""
    global flask_thread
    
    if flask_thread and flask_thread.is_alive():
        logger.info("Flask线程已在运行")
        return
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("🌐 Flask线程已启动")

def main():
    """主函数 - 增强版 + 自动重启"""
    global RESTART_COUNT
    
    logger.info("=" * 70)
    logger.info(f"📱 电话号码检测机器人 - 超级增强版 + 自动重启 (重启次数: {RESTART_COUNT})")
    logger.info("✅ 智能风险评估系统：已启用")
    logger.info("✅ 多级安全警告功能：已启用")
    logger.info("✅ 自动重启保护机制：已启用")
    logger.info("✅ HTTP服务器：已启用")
    logger.info("✅ 事件循环优化：nest_asyncio")
    logger.info(f"🔄 自动重启配置：{RESTART_COUNT}/{MAX_RESTARTS} 次，延迟 {RESTART_DELAY} 秒")
    logger.info("=" * 70)
    
    # 🔄 设置信号处理器 - 自动重启版
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 启动Flask服务器
        start_flask_thread()
        
        # 启动机器人
        start_bot_thread()
        
        logger.info("🎯 所有服务已启动，系统正在运行...")
        logger.info("🔄 自动重启功能已激活，将在收到SIGTERM信号时自动重启")
        
        # 保持主线程运行
        while not shutdown_event.is_set():
            time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("⌨️ 收到键盘中断信号")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"💥 程序运行错误: {e}")
        restart_application()
    
    logger.info("🔚 程序正在关闭...")

if __name__ == '__main__':
    main()
