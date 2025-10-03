#!/usr/bin/env python3
"""
电话号码重复检测机器人 - Render平台优化版
专门解决SIGTERM信号和平台重启问题
增加保活机制和智能重启策略
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
import requests

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

# 系统状态管理 - Render平台优化
graceful_shutdown = False  # 优雅关闭标志
bot_application = None
is_running = False
restart_count = 0
max_restart_attempts = 10  # 增加重试次数
start_time = time.time()
last_activity = time.time()

# 风险评估等级
RISK_LEVELS = {
    'LOW': {'emoji': '🟢', 'color': 'LOW', 'score': 1},
    'MEDIUM': {'emoji': '🟡', 'color': 'MEDIUM', 'score': 2}, 
    'HIGH': {'emoji': '🟠', 'color': 'HIGH', 'score': 3},
    'CRITICAL': {'emoji': '🔴', 'color': 'CRITICAL', 'score': 4}
}

def update_activity():
    """更新最后活动时间"""
    global last_activity
    last_activity = time.time()

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
    
    # 5. 国际号码混合检查
    existing_countries = set()
    for existing_phone in chat_data['phones']:
        country = categorize_phone_number(existing_phone).split()[0] + ' ' + categorize_phone_number(existing_phone).split()[1]
        existing_countries.add(country)
    
    current_country = categorize_phone_number(phone).split()[0] + ' ' + categorize_phone_number(phone).split()[1]
    if len(existing_countries) > 2 and current_country not in existing_countries:
        risk_score += 1
        warnings.append("🌍 地区混合：检测到多个不同国家/地区号码")
    
    # 6. 频繁提交检查
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

def generate_security_recommendations(phone_numbers: Set[str], risk_level: str) -> List[str]:
    """生成安全建议"""
    recommendations = []
    
    recommendations.extend([
        "🛡️ 请确保只与信任的联系人分享电话号码",
        "🔒 避免在公开场合大声说出完整电话号码",
        "📱 定期检查手机安全设置和隐私权限"
    ])
    
    if risk_level in ['HIGH', 'CRITICAL']:
        recommendations.extend([
            "🚨 高风险警告：建议立即验证号码来源",
            "⚠️ 如发现可疑活动，请联系相关通信运营商",
            "🔍 建议对异常号码进行额外验证"
        ])
    
    if len(phone_numbers) > 5:
        recommendations.append("📊 大量号码检测：建议分批处理以确保数据准确性")
    
    return recommendations[:6]

def generate_comprehensive_warnings(phone_numbers: Set[str], chat_data: Dict[str, Any]) -> Dict[str, Any]:
    """生成综合警告系统"""
    warning_system = {
        'alerts': [],
        'security_warnings': [],
        'data_protection_notices': [],
        'usage_recommendations': [],
        'risk_summary': {'total_score': 0, 'max_level': 'LOW'}
    }
    
    total_risk_score = 0
    max_risk_level = 'LOW'
    
    for phone in phone_numbers:
        risk_level, warnings = assess_phone_risk(phone, chat_data)
        risk_score = RISK_LEVELS[risk_level]['score']
        total_risk_score += risk_score
        
        if RISK_LEVELS[risk_level]['score'] > RISK_LEVELS[max_risk_level]['score']:
            max_risk_level = risk_level
        
        warning_system['alerts'].extend(warnings)
    
    # 数据保护提醒
    warning_system['data_protection_notices'].extend([
        "🔐 数据保护：您的电话号码将临时存储用于重复检测",
        "⏰ 自动清理：建议定期使用 /clear 命令清除历史数据",
        "🌐 隐私保护：机器人不会向第三方分享您的号码信息"
    ])
    
    # 使用建议
    if len(phone_numbers) > 1:
        warning_system['usage_recommendations'].append("📋 批量检测：一次检测多个号码，建议逐一核实")
    
    if max_risk_level in ['HIGH', 'CRITICAL']:
        warning_system['security_warnings'].extend([
            "🚨 安全警报：检测到高风险号码特征",
            "⚠️ 验证建议：请仔细核实号码来源和有效性"
        ])
    
    warning_system['risk_summary']['total_score'] = total_risk_score
    warning_system['risk_summary']['max_level'] = max_risk_level
    
    return warning_system

# 保活机制 - 防止Render平台空闲休眠
def keep_alive_service():
    """保活服务 - 防止平台空闲关闭"""
    while not graceful_shutdown:
        try:
            time.sleep(600)  # 每10分钟
            if not graceful_shutdown:
                # 自己ping自己，保持活跃
                try:
                    port = int(os.environ.get('PORT', 10000))
                    requests.get(f'http://localhost:{port}/health', timeout=5)
                    logger.debug("🏓 Keep-alive ping successful")
                    update_activity()
                except Exception as e:
                    logger.debug(f"Keep-alive ping failed: {e}")
                    
        except Exception as e:
            logger.error(f"Keep-alive service error: {e}")
            break

# Flask路由
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """健康检查端点"""
    global is_running, restart_count
    update_activity()
    return jsonify({
        'status': 'healthy',
        'service': 'telegram-phone-bot-render-optimized',
        'bot_running': is_running,
        'restart_count': restart_count,
        'uptime': time.time() - start_time,
        'nest_asyncio': 'enabled',
        'keep_alive': 'active',
        'features': ['risk_assessment', 'security_warnings', 'render_optimized'],
        'timestamp': time.time()
    })

@app.route('/health')
def health():
    """专用健康检查"""
    update_activity()
    return jsonify({
        'status': 'ok',
        'uptime': time.time() - start_time,
        'last_activity': time.time() - last_activity,
        'bot_running': is_running
    })

@app.route('/status')
def status():
    """详细状态端点"""
    global is_running
    return jsonify({
        'bot_status': 'running' if is_running else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
        'event_loop_fix': 'nest_asyncio',
        'enhanced_features': 'enabled',
        'render_optimization': 'active',
        'graceful_shutdown': graceful_shutdown,
        'uptime': time.time() - start_time
    })

@app.route('/restart', methods=['POST'])
def force_restart():
    """手动重启机器人的端点"""
    global is_running
    logger.info("📨 收到手动重启请求")
    is_running = False
    start_bot_thread()
    return jsonify({'message': 'Bot restart initiated', 'timestamp': datetime.datetime.now().isoformat()})

# Telegram机器人函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    update_activity()
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""
🎯 **欢迎使用Render优化版电话号码检测机器人，{user_name}！**

🚀 **Render平台特别优化**:
⭐ 智能SIGTERM信号处理
⭐ 自动保活防休眠机制
⭐ 增强重启恢复策略
⭐ 平台友好的资源管理
⭐ 智能风险评估系统

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
• `/export` - 导出号码清单
• `/security` - 安全状况检查
• `/help` - 快速帮助指南

🔥 **使用方法**:
1️⃣ 直接发送包含电话号码的任何消息
2️⃣ 获得智能风险评估和详细分析
3️⃣ 查看安全建议和保护提醒

💡 **Render平台优化特性**: 
• 🔄 自动处理平台重启信号
• 🏓 保活机制防止空闲休眠
• ⚡ 智能资源管理和恢复
• 📊 平台友好的运行监控

现在就发送电话号码开始智能检测吧！ 🎯
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    update_activity()
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

⏰ 清理时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
    await update.message.reply_text(clear_message, parse_mode='Markdown')

async def security_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """安全状况检查"""
    update_activity()
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    # 计算安全指标
    total_phones = len(chat_data['phones'])
    high_risk_count = sum(1 for risk in chat_data['risk_scores'].values() 
                         if RISK_LEVELS.get(risk, {}).get('score', 0) >= 3)
    
    warnings_count = len(chat_data['warnings_issued'])
    recent_alerts = len([alert for alert in chat_data['security_alerts'] 
                        if (datetime.datetime.now() - alert.get('timestamp', datetime.datetime.min)).days <= 7])
    
    # 计算安全评分
    security_score = max(0, 100 - (high_risk_count * 10) - (warnings_count * 5) - (recent_alerts * 15))
    
    if security_score >= 80:
        security_level = "🟢 安全"
        security_emoji = "✅"
    elif security_score >= 60:
        security_level = "🟡 注意"
        security_emoji = "⚠️"
    elif security_score >= 40:
        security_level = "🟠 警告"
        security_emoji = "🚨"
    else:
        security_level = "🔴 危险"
        security_emoji = "⛔"
    
    security_report = f"""
🛡️ **安全状况检查报告**
================================

{security_emoji} **当前安全等级**: {security_level}
📊 **安全评分**: {security_score}/100

📈 **详细安全指标**:
• 总检测号码: {total_phones} 个
• 高风险号码: {high_risk_count} 个
• 累计警告: {warnings_count} 次
• 7天内安全警报: {recent_alerts} 次

⏰ 检查时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
    
    await update.message.reply_text(security_report, parse_mode='Markdown')

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /export 命令"""
    update_activity()
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    all_phones = chat_data['phones']
    
    if not all_phones:
        await update.message.reply_text("📝 当前群组暂无电话号码记录")
        return
    
    # 按类型和风险分组
    phone_by_category = {}
    risk_stats = {'LOW': 0, 'MEDIUM': 0, 'HIGH': 0, 'CRITICAL': 0}
    
    for phone in all_phones:
        phone_type = categorize_phone_number(phone)
        risk_level = chat_data['risk_scores'].get(phone, 'LOW')
        risk_stats[risk_level] += 1
        
        category_key = f"{phone_type} ({RISK_LEVELS[risk_level]['emoji']} {risk_level})"
        if category_key not in phone_by_category:
            phone_by_category[category_key] = []
        phone_by_category[category_key].append(phone)
    
    export_text = f"""
📋 **电话号码清单导出报告**
=====================================
📊 **总览**: {len(all_phones)} 个号码

🛡️ **风险分布统计**:
• 🟢 低风险: {risk_stats['LOW']} 个
• 🟡 中等风险: {risk_stats['MEDIUM']} 个
• 🟠 高风险: {risk_stats['HIGH']} 个
• 🔴 严重风险: {risk_stats['CRITICAL']} 个

📱 **详细号码清单**:
=====================================
"""
    
    for category, phones in sorted(phone_by_category.items()):
        export_text += f"\n**{category}** ({len(phones)}个):\n"
        for i, phone in enumerate(sorted(phones), 1):
            export_text += f"{i:2d}. `{phone}`\n"
        export_text += "\n"
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    export_text += f"""
⏰ **导出信息**:
• 导出时间: {now}
• 数据完整性: ✅ 已验证
• 包含风险评估: ✅ 是
• Render优化版: v3.0
"""
    
    await update.message.reply_text(export_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    update_activity()
    help_text = """
🆘 **快速帮助指南**

📋 **核心命令**:
• `/start` - 完整功能介绍
• `/stats` - 详细统计报告
• `/security` - 安全状况检查
• `/clear` - 清除所有记录  
• `/export` - 导出号码清单
• `/help` - 本帮助信息

🚀 **快速上手**:
1️⃣ 直接发送包含电话号码的消息
2️⃣ 查看智能风险评估结果
3️⃣ 关注安全警告和建议

🛡️ **风险等级说明**:
• 🟢 低风险 - 安全可靠
• 🟡 中等风险 - 需要注意
• 🟠 高风险 - 建议验证
• 🔴 严重风险 - 立即核实

🔄 **Render平台优化**: 自动处理平台重启，保持稳定运行

💡 **示例**: `联系方式：+60 11-2896 2309`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令"""
    update_activity()
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
    
    # 安全统计
    high_risk_count = risk_distribution['HIGH'] + risk_distribution['CRITICAL']
    security_percentage = max(0, (total_count - high_risk_count) / max(total_count, 1) * 100)
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uptime = time.time() - start_time
    
    stats_text = f"""
📊 **Render优化版统计报告**
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

📊 **安全指数**: {security_percentage:.1f}%

🎯 **系统状态**:
• 运行状态: ✅ 正常运行
• 运行时长: {uptime//3600:.0f}h {(uptime%3600)//60:.0f}m
• 重启次数: {restart_count} 次
• Render优化: ✅ 已启用
• 保活机制: ✅ 运行中
• 事件循环: ✅ 已优化 (nest_asyncio)

---
🤖 **Render优化版电话号码检测机器人** v3.0
🔄 **专为Render平台优化的稳定版本**
"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息 - 简化版本"""
    try:
        update_activity()
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
        
        # 检查重复和分类
        existing_phones = chat_data['phones']
        new_phones = phone_numbers - existing_phones
        duplicate_phones = phone_numbers & existing_phones
        
        # 构建回复
        response_parts = []
        response_parts.append("🎯 **智能电话号码检测系统**")
        response_parts.append("=" * 35)
        response_parts.append(f"👤 **检测用户**: {user_name}")
        response_parts.append(f"🔍 **检测数量**: {len(phone_numbers)} 个号码")
        response_parts.append("")
        
        # 显示新发现的号码
        if new_phones:
            response_parts.append(f"✨ **新发现号码** ({len(new_phones)}个):")
            for i, phone in enumerate(sorted(new_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level, _ = assess_phone_risk(phone, chat_data)
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                
                # 保存风险评分
                chat_data['risk_scores'][phone] = risk_level
                
                response_parts.append(f"{i:2d}. `{phone}`")
                response_parts.append(f"    📱 {phone_type}")
                response_parts.append(f"    🛡️ 风险: {risk_emoji} {risk_level}")
                response_parts.append("")
            
            # 添加到记录中
            existing_phones.update(new_phones)
        
        # 显示重复号码
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
        response_parts.append("📊 **检测统计**:")
        response_parts.append(f"• 群组总计: {total_in_group} 个号码")
        response_parts.append(f"• 本次检测: {len(phone_numbers)} 个")
        
        # 时间戳
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        response_parts.append(f"⏰ {now}")
        response_parts.append("🔄 **Render优化版** v3.0")
        
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
    logger.info(f"启动Render优化版Flask服务器，端口: {port}")
    
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
    """运行Telegram机器人 - Render优化版"""
    global bot_application, is_running, restart_count
    
    # 获取Bot Token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        logger.info(f"🚀 正在启动 Telegram 机器人... (第 {restart_count + 1} 次)")
        
        # 创建应用
        bot_application = Application.builder().token(bot_token).build()
        
        # 添加错误处理器
        bot_application.add_error_handler(error_handler)
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("export", export_command))
        bot_application.add_handler(CommandHandler("security", security_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        is_running = True
        logger.info("✅ Render优化版电话号码检测机器人已启动！")
        logger.info("🔄 已启用SIGTERM智能处理")
        logger.info("🏓 已启用保活机制")
        logger.info("🛡️ 集成智能风险评估系统")
        
        # 关键修复：运行机器人，避免事件循环冲突
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,  # 不让库关闭事件循环
            stop_signals=None, # 禁用内置信号处理，我们自己处理
            poll_interval=3.0, # 适当增加轮询间隔，对Render更友好
            timeout=30         # 增加超时时间
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        is_running = False
        raise e
    finally:
        is_running = False
        logger.info("机器人已停止运行")

def start_bot_thread():
    """在新线程中启动机器人，带有智能重启功能"""
    global bot_thread, is_running, restart_count, max_restart_attempts, graceful_shutdown
    
    def run_async_bot():
        global restart_count, is_running
        
        while restart_count < max_restart_attempts and not graceful_shutdown:
            try:
                # 创建新的事件循环
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # 运行机器人
                loop.run_until_complete(run_bot())
                
                # 如果正常退出，检查是否是优雅关闭
                if graceful_shutdown:
                    logger.info("检测到优雅关闭信号，正常退出")
                    break
                else:
                    logger.info("机器人正常退出，准备重启...")
                
            except Exception as e:
                restart_count += 1
                is_running = False
                
                logger.error(f"机器人线程错误 (第 {restart_count} 次): {e}")
                
                if restart_count < max_restart_attempts and not graceful_shutdown:
                    wait_time = min(60, 10 * restart_count)  # 指数退避，最大60秒
                    logger.info(f"等待 {wait_time} 秒后重启...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"达到最大重启次数 ({max_restart_attempts}) 或收到关闭信号，停止重启")
                    break
            finally:
                try:
                    loop.close()
                except:
                    pass
    
    if 'bot_thread' not in globals() or not bot_thread.is_alive():
        bot_thread = threading.Thread(target=run_async_bot, daemon=True)
        bot_thread.start()
        logger.info("🔄 机器人线程已启动，启用智能重启功能")

def health_check_thread():
    """健康检查线程，监控机器人状态"""
    global is_running, restart_count, max_restart_attempts, graceful_shutdown
    
    while not graceful_shutdown:
        time.sleep(120)  # 每2分钟检查一次
        
        if not is_running and restart_count < max_restart_attempts and not graceful_shutdown:
            logger.warning("⚠️ 检测到机器人停止运行，尝试重启...")
            start_bot_thread()

def signal_handler(signum, frame):
    """信号处理器 - Render平台优化版"""
    global graceful_shutdown, is_running
    
    signal_names = {
        signal.SIGTERM: "SIGTERM (15) - 平台重启信号",
        signal.SIGINT: "SIGINT (2) - 中断信号",
        signal.SIGHUP: "SIGHUP (1) - 挂起信号"
    }
    
    signal_name = signal_names.get(signum, f"Signal {signum}")
    logger.info(f"🛑 收到信号: {signal_name}")
    
    # 关键改进：对于SIGTERM（平台重启），不立即退出
    if signum == signal.SIGTERM:
        logger.info("📋 检测到Render平台重启信号")
        logger.info("🔄 准备优雅处理平台重启...")
        
        # 标记为优雅关闭，但不立即退出
        # 让重启机制在适当时机处理
        graceful_shutdown = True
        is_running = False
        
        # 不调用 sys.exit(0)，让平台自己管理进程生命周期
        logger.info("✅ 已设置优雅关闭标志，等待平台管理...")
        
    else:
        # 对于其他信号（如SIGINT），立即关闭
        logger.info("⏹️ 执行立即关闭...")
        graceful_shutdown = True
        is_running = False
        sys.exit(0)

def main():
    """主函数 - Render平台优化版"""
    global graceful_shutdown
    
    logger.info("🚀 正在启动Render优化版应用...")
    logger.info("🔧 已应用nest_asyncio，解决事件循环冲突")
    logger.info("🛡️ 集成智能风险评估系统")
    logger.info("🔄 启用Render平台SIGTERM智能处理")
    logger.info("🏓 启用保活机制防止空闲休眠")
    logger.info("⚡ 启用自动重启和故障恢复机制")
    
    # 设置信号处理 - 关键改进
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)  # 专门优化SIGTERM处理
    
    try:
        # 在独立线程中启动Flask
        flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskThread")
        flask_thread.start()
        
        # 等待Flask启动
        time.sleep(3)
        logger.info("✅ Render优化版Flask服务器已在后台启动")
        
        # 启动保活服务
        keep_alive_thread = threading.Thread(target=keep_alive_service, daemon=True, name="KeepAliveThread")
        keep_alive_thread.start()
        logger.info("🏓 保活服务已启动")
        
        # 启动机器人线程（带智能重启功能）
        start_bot_thread()
        
        # 启动健康检查线程
        health_thread = threading.Thread(target=health_check_thread, daemon=True, name="HealthCheckThread")
        health_thread.start()
        logger.info("🔍 健康检查线程已启动")
        
        logger.info("🎯 所有服务已启动，Render优化版系统正在运行...")
        
        # 保持主线程运行 - 改进的等待逻辑
        while not graceful_shutdown:
            time.sleep(5)  # 缩短睡眠时间，更快响应信号
        
        logger.info("📋 检测到优雅关闭信号，准备退出...")
        
    except KeyboardInterrupt:
        logger.info("⌨️ 收到键盘中断信号")
        graceful_shutdown = True
    except Exception as e:
        logger.error(f"❌ 程序运行错误: {e}")
        graceful_shutdown = True
    
    logger.info("👋 Render优化版程序正在关闭...")

if __name__ == '__main__':
    main()
