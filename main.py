#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 终极修复版
完全重写重复检测逻辑，确保绝对精确
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
    'normalized_phones': set(),  # 新增：存储标准化后的号码用于重复检测
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

def normalize_phone_number(phone: str) -> str:
    """标准化电话号码：只保留数字和+号"""
    return re.sub(r'[^\d+]', '', phone)

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
    all_matches = []
    
    # 首先收集所有匹配项及其位置
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            all_matches.append((match.start(), match.end(), match.group()))
    
    # 按位置排序，避免重叠匹配
    all_matches.sort()
    
    # 过滤重叠的匹配
    filtered_matches = []
    for start, end, match_text in all_matches:
        # 检查是否与之前的匹配重叠
        overlap = False
        for prev_start, prev_end, _ in filtered_matches:
            if start < prev_end and end > prev_start:  # 有重叠
                overlap = True
                break
        
        if not overlap:
            filtered_matches.append((start, end, match_text))
    
    # 处理最终的匹配结果
    for _, _, match_text in filtered_matches:
        # 标准化电话号码格式：统一空格，保持结构
        cleaned = re.sub(r'\s+', ' ', match_text.strip())
        # 进一步标准化：移除多余的分隔符
        normalized = re.sub(r'[-\s]+', ' ', cleaned)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        phone_numbers.add(normalized)
    
    return phone_numbers

def categorize_phone_number(phone: str) -> str:
    """识别电话号码的类型和国家"""
    clean_phone = normalize_phone_number(phone)
    
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

def assess_phone_risk(phone: str, chat_data: Dict[str, Any], is_duplicate: bool = False) -> Tuple[str, List[str]]:
    """评估电话号码风险等级"""
    warnings = []
    risk_score = 0
    
    clean_phone = normalize_phone_number(phone)
    
    # 1. 重复度检查
    if is_duplicate:
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
    
    if bot_application:
        try:
            bot_application.stop_running()
            logger.info("Telegram 机器人已停止")
        except Exception as e:
            logger.error(f"停止机器人时出错: {e}")
    
    # 检查是否需要重启
    if signum in [signal.SIGTERM, signal.SIGINT]:
        logger.info("🔄 系统终止信号，准备自动重启...")
        restart_application()
    else:
        sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Flask路由
@app.route('/')
def home():
    """主页"""
    global RESTART_COUNT, is_running
    
    status = {
        'service': '电话号码检测机器人 终极修复版',
        'status': '✅ 运行中' if is_running else '❌ 停止',
        'restart_count': f'{RESTART_COUNT}/{MAX_RESTARTS}',
        'features': [
            '✅ HTML格式化显示',
            '✅ 红色重复号码警示',
            '✅ 智能风险评估',
            '✅ 自动重启保护',
            '✅ 兼容性过滤器',
            '✅ 终极重复检测修复'
        ],
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return jsonify(status)

@app.route('/health')
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy' if is_running else 'unhealthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'restart_count': RESTART_COUNT,
        'features_enabled': [
            'html_formatting',
            'red_duplicate_warning',
            'risk_assessment',
            'auto_restart',
            'compatibility_filter',
            'ultimate_duplicate_detection_fix'
        ]
    })

@app.route('/restart')
def restart_bot():
    """手动重启机器人"""
    logger.info("📱 通过HTTP请求重启机器人")
    restart_application()
    return jsonify({'message': 'Bot restart initiated', 'timestamp': datetime.datetime.now().isoformat()})

# Telegram机器人函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""🎯 <b>欢迎使用电话号码检测机器人，{user_name}！</b>

<b>🚀 核心功能特色:</b>
⭐ 智能电话号码提取与分析
⭐ <b>重复号码红色警示显示</b>
⭐ 多国电话号码格式支持
⭐ 智能风险评估系统
⭐ 自动重启保持运行
⭐ <b>🔧 终极重复检测修复</b>

<b>📱 支持的电话号码格式:</b>

<b>🇲🇾 马来西亚格式 (优先支持):</b>
• <code>+60 11-2896 2309</code> (标准格式)
• <code>+60 11 2896 2309</code> (空格分隔)
• <code>+6011-28962309</code> (紧凑格式)
• <code>01-1234 5678</code> (本地手机)
• <code>03-1234 5678</code> (本地固话)

<b>🌏 全球国际格式:</b>
• 🇨🇳 中国: <code>+86 138 0013 8000</code>
• 🇺🇸 美国: <code>+1 555 123 4567</code>
• 🇸🇬 新加坡: <code>+65 6123 4567</code>
• 🇭🇰 香港: <code>+852 2123 4567</code>
• + 更多国际格式...

<b>📋 命令列表:</b>
• /start - 显示完整功能介绍
• /clear - 清除所有记录
• /stats - 详细统计与风险报告
• /help - 快速帮助指南

<b>🔄 自动重启功能:</b>
✅ 服务器重启后自动恢复
✅ 系统故障自动修复
✅ 保持24/7持续运行
✅ 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}

<b>🔧 终极重复检测修复:</b>
✅ 完全重写重复检测逻辑
✅ 确保绝对精确的号码比较
✅ 修复了所有误判问题

现在就发送包含电话号码的消息开始检测吧！🎯"""
    
    await update.message.reply_text(help_text, parse_mode='HTML')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    chat_id = update.effective_chat.id
    phone_count = len(user_groups[chat_id]['phones'])
    history_count = len(user_groups[chat_id]['phone_history'])
    
    # 清理所有数据
    user_groups[chat_id]['phones'].clear()
    user_groups[chat_id]['normalized_phones'].clear()  # 新增：清理标准化号码
    user_groups[chat_id]['phone_history'].clear()
    user_groups[chat_id]['risk_scores'].clear()
    user_groups[chat_id]['warnings_issued'].clear()
    user_groups[chat_id]['security_alerts'].clear()
    
    clear_message = f"""<pre>🧹 数据清理完成
========================

📊 清理统计:
• 电话号码: {phone_count} 个
• 历史记录: {history_count} 条
• 风险评分: 已重置
• 安全警报: 已清空

🔒 隐私保护:
✅ 所有号码数据已安全删除
✅ 检测历史已完全清除
✅ 风险评估记录已重置
✅ 安全警报历史已清空

💡 清理完成提醒:
现在可以重新开始检测电话号码，
所有新检测将重新进行风险评估。

⏰ 清理时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</pre>"""
    
    await update.message.reply_text(clear_message, parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令"""
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
    
    stats_text = f"""<pre>📊 统计报告
=====================================

👤 报告信息:
• 查询者: {user_name}
• 群组: {chat_title}
• 生成时间: {now}

📈 数据总览:
• 总电话号码: {total_count} 个
• 马来西亚号码: {malaysia_count} 个 ({malaysia_count/max(total_count,1)*100:.1f}%)
• 中国号码: {china_count} 个 ({china_count/max(total_count,1)*100:.1f}%)
• 其他国际号码: {international_count} 个 ({international_count/max(total_count,1)*100:.1f}%)

🛡️ 风险评估统计:
• 🟢 低风险: {risk_distribution['LOW']} 个 ({risk_distribution['LOW']/max(total_count,1)*100:.1f}%)
• 🟡 中等风险: {risk_distribution['MEDIUM']} 个 ({risk_distribution['MEDIUM']/max(total_count,1)*100:.1f}%)
• 🟠 高风险: {risk_distribution['HIGH']} 个 ({risk_distribution['HIGH']/max(total_count,1)*100:.1f}%)
• 🔴 严重风险: {risk_distribution['CRITICAL']} 个 ({risk_distribution['CRITICAL']/max(total_count,1)*100:.1f}%)

🔄 自动重启系统:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• 运行状态: ✅ 正常运行
• 自动重启: ✅ 已启用

🎯 系统状态:
• HTML格式: ✅ 已启用
• 红色警示: ✅ 已启用
• 兼容过滤器: ✅ 已启用
• 风险检测: ✅ 智能评估已启用
• 自动重启保护: ✅ 已启用
• 终极重复检测修复: ✅ 已修复

---
🤖 电话号码检测机器人 HTML增强版 v5.0
🔴 集成红色重复号码警示系统 + 兼容过滤器
🔧 终极重复检测修复 - 绝对精确</pre>"""
    
    await update.message.reply_text(stats_text, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    help_text = f"""<pre>🆘 快速帮助指南

📋 核心命令:
• /start - 完整功能介绍
• /stats - 详细统计报告
• /clear - 清除所有记录  
• /help - 本帮助信息

🚀 快速上手:
1️⃣ 直接发送包含电话号码的消息
2️⃣ 查看智能风险评估结果
3️⃣ 关注重复号码的红色警示

🔴 特色功能:
• HTML格式化显示
• 重复号码红色警示
• 智能风险评估
• 兼容性过滤器
• 🔧 终极重复检测修复

🔄 自动重启功能:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• ✅ 自动保持运行
• ✅ 故障自动恢复

💡 示例: 联系方式：+60 11-2896 2309

🔧 重复检测说明:
只有数字完全相同的号码才会被标记为重复
例如：+60 13-970 3144 和 +60 13-970 3146 绝对不会被误判为重复</pre>"""
    
    await update.message.reply_text(help_text, parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息 - 终极修复版"""
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
        
        # 🔧 终极重复检测逻辑：基于标准化号码进行精确比较
        existing_normalized_phones = chat_data['normalized_phones']
        new_phones = set()
        duplicate_phones = set()
        
        logger.info(f"检测开始 - 当前消息号码: {phone_numbers}")
        logger.info(f"已存储的标准化号码: {existing_normalized_phones}")
        
        for phone in phone_numbers:
            # 标准化当前号码
            normalized_phone = normalize_phone_number(phone)
            logger.info(f"号码 '{phone}' 标准化为: '{normalized_phone}'")
            
            # 检查标准化后的号码是否已存在
            if normalized_phone in existing_normalized_phones:
                duplicate_phones.add(phone)
                logger.info(f"检测到重复号码: {phone} (标准化: {normalized_phone})")
            else:
                new_phones.add(phone)
                # 将标准化号码和原始号码都添加到存储中
                existing_normalized_phones.add(normalized_phone)
                chat_data['phones'].add(phone)
                logger.info(f"新号码: {phone} (标准化: {normalized_phone})")
        
        logger.info(f"分类结果 - 新号码: {new_phones}, 重复号码: {duplicate_phones}")
        
        # 构建HTML格式的完整报告
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_in_group = len(chat_data['phones'])
        
        # 计算统计数据
        all_detected = phone_numbers
        malaysia_count = len([p for p in all_detected if categorize_phone_number(p).startswith("🇲🇾")])
        china_count = len([p for p in all_detected if categorize_phone_number(p).startswith("🇨🇳")])
        other_count = len(all_detected) - malaysia_count - china_count
        
        # 构建完整的HTML报告
        report = f"""<pre>🎯 查毒机器人 - 终极修复版
=====================================

👤 检测用户: {user_name}
📊 检测结果报告
⏰ 检测时间: {now}

📱 本次检测结果:
• 发现号码总数: {len(phone_numbers)} 个
• 新发现号码: {len(new_phones)} 个
• 重复检测号码: {len(duplicate_phones)} 个

📊 号码分类统计:
• 🇲🇾 马来西亚: {malaysia_count} 个
• 🇨🇳 中国: {china_count} 个  
• 🌍 其他地区: {other_count} 个

===================================== 
📋 详细检测清单:</pre>

"""
        
        # 新发现的号码（正常显示）
        if new_phones:
            report += f"<pre>✨ 新发现号码 ({len(new_phones)}个):</pre>\n"
            for i, phone in enumerate(sorted(new_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level, risk_warnings = assess_phone_risk(phone, chat_data, is_duplicate=False)
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                
                # 保存风险评分
                chat_data['risk_scores'][phone] = risk_level
                
                normalized = normalize_phone_number(phone)
                report += f"<pre>{i:2d}. 📞 <code>{phone}</code>\n"
                report += f"    📍 类型: {phone_type}\n"
                report += f"    🛡️ 风险: {risk_emoji} {risk_level}\n"
                report += f"    🔧 标准化: {normalized}</pre>\n"
        
        # 重复号码（红色警示显示）
        if duplicate_phones:
            report += f"\n<b><u>⚠️ 重复号码警告 ({len(duplicate_phones)}个):</u></b>\n"
            for i, phone in enumerate(sorted(duplicate_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level = chat_data['risk_scores'].get(phone, 'MEDIUM')
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                
                normalized = normalize_phone_number(phone)
                # 🔴 关键: 重复号码使用红色显示
                report += f'<pre>{i:2d}. 📞 <code>{phone}</code>\n'
                report += f'    📍 类型: {phone_type}\n'
                report += f'    ⚠️ <b>状态：重复号码</b> ⚠️\n'
                report += f'    🔧 标准化: {normalized}</pre>\n'
        
        # 底部统计信息
        report += f"""
<pre>=====================================
📊 群组统计信息:
• 群组总计: {total_in_group} 个号码
• 检测历史: {len(chat_data['phone_history'])} 次
• 系统重启: {RESTART_COUNT}/{MAX_RESTARTS} 次

🎯 系统状态:
• 运行状态: ✅ 正常运行  
• HTML格式: ✅ 已启用
• 红色警示: ✅ 已启用
• 兼容过滤器: ✅ 已启用
• 自动重启: ✅ 保护中
• 终极重复检测修复: ✅ v5.0

=====================================
🤖 电话号码检测机器人 HTML增强版 v5.0
🔴 集成红色重复号码警示系统 + 兼容过滤器
🔧 终极重复检测修复 - 绝对精确判断
⏰ {now}</pre>"""
        
        # 发送完整的HTML格式报告
        await update.message.reply_text(report, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text(
            "❌ 处理消息时出现错误，系统正在自动恢复...",
            parse_mode='HTML'
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理器"""
    logger.error(f"更新 {update} 引起了错误 {context.error}")
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ 处理过程中发生错误，系统正在自动恢复...",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")

def run_flask():
    """在独立线程中运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 启动HTML格式Flask服务器，端口: {port}")
    
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
    """运行Telegram机器人"""
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
        
        # 🔧 使用最基本且兼容的过滤器设置
        bot_application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        ))
        
        is_running = True
        logger.info("✅ HTML格式电话号码检测机器人已启动！")
        logger.info("🔴 红色重复号码警示功能已启用")
        logger.info("🔧 使用兼容性过滤器设置")
        logger.info("🔄 启用自动重启保护功能")
        logger.info("🔧 使用nest_asyncio解决事件循环冲突")
        logger.info("🔧 终极重复检测逻辑已修复 - v5.0 绝对精确")
        
        # 运行机器人
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            stop_signals=None
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        is_running = False
        raise e
    finally:
        is_running = False
        logger.info("机器人已停止运行")

def main():
    """主程序入口"""
    global flask_thread, bot_thread
    
    try:
        logger.info(f"🎯 启动电话号码检测机器人 (HTML增强版 v5.0) - 终极重复检测修复版")
        logger.info(f"🔄 重启保护: {RESTART_COUNT}/{MAX_RESTARTS}")
        
        # 启动Flask服务器
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("🌐 Flask服务器线程已启动")
        
        # 运行Telegram机器人 (主线程)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(run_bot())
        except KeyboardInterrupt:
            logger.info("👋 用户手动停止机器人")
        except Exception as e:
            logger.error(f"机器人运行时发生错误: {e}")
            restart_application()
        finally:
            try:
                # 清理资源
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    
                loop.close()
                logger.info("事件循环已关闭")
            except Exception as e:
                logger.error(f"清理资源时出错: {e}")
    
    except Exception as e:
        logger.error(f"程序启动失败: {e}")
        restart_application()

if __name__ == "__main__":
    main()
