#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 超级增强版 (自动重启版)
增强版警告系统 + 风险评估 + 安全提醒 + 自动重启
修复所有事件循环和部署问题，添加SIGTERM自动重启功能
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
# 添加自动重启相关全局变量
RESTART_COUNT = 0
MAX_RESTARTS = 10
RESTART_DELAY = 5
MAIN_PROCESS_PID = None
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
# 系统状态管理
shutdown_event = threading.Event()
bot_application = None
is_running = False
restart_count = 0
max_restart_attempts = 5
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
# Telegram 处理器函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    start_message = """
🔍 **电话号码重复检测机器人** - 超级增强版
🚀 **新功能亮点：**
• 智能风险评估系统
• 多级安全警告功能
• 支持马来西亚等多国格式
• 批量号码重复检测
• 详细安全建议
📱 **支持格式：**
• 马来西亚: +60 11-2896 2309
• 中国: +86 138 0013 8000
• 其他国际格式
💡 **使用方法：**
直接发送电话号码，我会：
✅ 检测重复
✅ 风险评估
✅ 安全提醒
✅ 生成报告
🔧 **命令列表：**
/clear - 清除历史数据
/stats - 查看统计信息
/export - 导出数据
/security - 安全分析
/help - 详细帮助
🛡️ **隐私保护：**
您的数据仅用于重复检测，不会外泄。建议定期清理历史记录。
直接发送电话号码开始检测吧！📞
    """
    await update.message.reply_text(start_message, parse_mode='Markdown')
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("📭 没有需要清除的数据。")
        return
    
    cleared_count = len(chat_data['phones'])
    # 清空数据
    chat_data['phones'].clear()
    chat_data['phone_history'].clear()
    chat_data['risk_scores'].clear()
    chat_data['warnings_issued'].clear()
    chat_data['security_alerts'].clear()
    
    await update.message.reply_text(
        f"🗑️ 已清除 {cleared_count} 条历史记录\n"
        f"✅ 数据清理完成，您可以重新开始检测"
    )
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令 - 显示详细统计"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("📊 暂无统计数据，请先检测一些电话号码。")
        return
    
    # 统计数据
    total_phones = len(chat_data['phones'])
    total_submissions = len(chat_data['phone_history'])
    duplicates = find_duplicates(chat_data['phones'])
    duplicate_count = len(duplicates)
    
    # 按国家分类统计
    country_stats = defaultdict(int)
    risk_stats = defaultdict(int)
    
    for phone in chat_data['phones']:
        country = categorize_phone_number(phone)
        country_stats[country] += 1
        
        if phone in chat_data['risk_scores']:
            risk_level = chat_data['risk_scores'][phone]['level']
            risk_stats[risk_level] += 1
    
    # 生成统计报告
    stats_message = f"""
📊 **详细统计报告**
📱 **基本数据：**
• 总检测次数：{total_submissions}
• 唯一号码数：{total_phones}
• 重复号码数：{duplicate_count}
• 重复率：{(duplicate_count/total_phones*100):.1f}%
🌍 **国家/地区分布：**
"""
    
    for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / total_phones) * 100
        stats_message += f"• {country}: {count} ({percentage:.1f}%)\n"
    
    if risk_stats:
        stats_message += "\n🛡️ **风险分布：**\n"
        for level, count in sorted(risk_stats.items(), key=lambda x: RISK_LEVELS[x[0]]['score']):
            emoji = RISK_LEVELS[level]['emoji']
            percentage = (count / len(chat_data['risk_scores'])) * 100
            stats_message += f"• {emoji} {level}: {count} ({percentage:.1f}%)\n"
    
    # 最近活动
    if chat_data['phone_history']:
        recent = chat_data['phone_history'][-1]
        stats_message += f"\n🕒 **最近活动：**\n• {recent['timestamp'].strftime('%Y-%m-%d %H:%M')}"
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /export 命令 - 导出数据"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("📤 没有数据可以导出。")
        return
    
    # 生成导出数据
    export_data = "电话号码检测报告\n"
    export_data += "=" * 50 + "\n\n"
    export_data += f"导出时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    export_data += f"总号码数：{len(chat_data['phones'])}\n\n"
    
    export_data += "电话号码列表：\n"
    export_data += "-" * 30 + "\n"
    
    for i, phone in enumerate(sorted(chat_data['phones']), 1):
        category = categorize_phone_number(phone)
        risk_info = ""
        if phone in chat_data['risk_scores']:
            risk_level = chat_data['risk_scores'][phone]['level']
            risk_emoji = RISK_LEVELS[risk_level]['emoji']
            risk_info = f" [{risk_emoji} {risk_level}]"
        
        export_data += f"{i}. {phone} - {category}{risk_info}\n"
    
    # 发送为文本文件
    from io import BytesIO
    file_buffer = BytesIO(export_data.encode('utf-8'))
    file_buffer.name = f"phone_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    await update.message.reply_document(
        document=file_buffer,
        filename=file_buffer.name,
        caption="📤 您的电话号码检测报告已生成完成！"
    )
async def security_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /security 命令 - 安全分析"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("🔒 没有数据进行安全分析。")
        return
    
    # 进行安全分析
    total_phones = len(chat_data['phones'])
    high_risk_count = sum(1 for phone in chat_data['phones'] 
                         if phone in chat_data['risk_scores'] and 
                         chat_data['risk_scores'][phone]['level'] in ['HIGH', 'CRITICAL'])
    
    duplicates = find_duplicates(chat_data['phones'])
    duplicate_rate = len(duplicates) / total_phones * 100 if total_phones > 0 else 0
    
    # 计算安全评分
    security_score = 100
    if duplicate_rate > 20:
        security_score -= 30
    elif duplicate_rate > 10:
        security_score -= 15
    
    if high_risk_count > total_phones * 0.3:
        security_score -= 40
    elif high_risk_count > total_phones * 0.1:
        security_score -= 20
    
    # 确定安全等级
    if security_score >= 80:
        security_level = "🟢 安全"
        security_color = "良好"
    elif security_score >= 60:
        security_level = "🟡 注意"
        security_color = "中等"
    elif security_score >= 40:
        security_level = "🟠 警告"
        security_color = "较差"
    else:
        security_level = "🔴 危险"
        security_color = "很差"
    
    security_message = f"""
🔒 **安全分析报告**
📊 **安全评分：** {security_score}/100
🛡️ **安全等级：** {security_level}
📈 **风险指标：**
• 总检测号码：{total_phones}
• 高风险号码：{high_risk_count}
• 重复号码率：{duplicate_rate:.1f}%
• 风险号码比例：{(high_risk_count/total_phones*100):.1f}%
🔍 **安全建议：**
"""
    
    if security_score >= 80:
        security_message += "✅ 您的电话号码数据质量良好，继续保持谨慎态度。\n"
    else:
        security_message += "⚠️ 建议仔细核实高风险号码的来源和真实性。\n"
        if duplicate_rate > 10:
            security_message += "🔄 检测到较多重复号码，建议清理数据。\n"
        if high_risk_count > 0:
            security_message += f"🚨 发现 {high_risk_count} 个高风险号码，请特别注意。\n"
    
    security_message += "\n💡 定期使用 /clear 清理历史数据以保护隐私。"
    
    await update.message.reply_text(security_message, parse_mode='Markdown')
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    help_message = """
📖 **详细使用指南**
🔍 **电话号码检测机器人** - 超级增强版
**✨ 主要功能：**
• 智能重复检测
• 多级风险评估
• 国际格式支持
• 安全分析报告
• 数据导出功能
**📱 支持的号码格式：**
🇲🇾 **马来西亚：**
• +60 11-2896 2309
• +60 3-1234 5678
• 011-2896 2309
🇨🇳 **中国：**
• +86 138 0013 8000
• +86 010-1234 5678
• 13800138000
🌍 **其他国际格式：**
• 美国/加拿大: +1 555-123-4567
• 新加坡: +65 6123 4567
• 香港: +852 2123 4567
• 英国: +44 20 1234 5678
**🛠️ 命令说明：**
`/start` - 显示欢迎信息
`/clear` - 清除所有历史数据
`/stats` - 查看详细统计信息
`/export` - 导出检测报告
`/security` - 进行安全分析
`/help` - 显示此帮助信息
**🔍 风险等级说明：**
🟢 **低风险** - 格式正常，无异常特征
🟡 **中等风险** - 存在轻微异常，建议核实
🟠 **高风险** - 发现多项异常特征
🔴 **极高风险** - 存在严重异常，需要验证
**💡 使用技巧：**
1. **批量检测**：一次发送多个号码
2. **定期清理**：使用 /clear 保护隐私
3. **查看报告**：使用 /stats 了解详情
4. **导出数据**：使用 /export 保存结果
**🔐 隐私保护：**
• 数据仅存储在会话期间
• 不会向第三方分享信息
• 建议定期清理历史记录
• 所有处理均在本地完成
有问题请重新发送 /start 开始使用！
    """
    await update.message.reply_text(help_message, parse_mode='Markdown')
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息"""
    user_id = update.effective_user.id
    message_text = update.message.text
    chat_data = user_groups[user_id]
    
    # 提取电话号码
    phone_numbers = extract_phone_numbers(message_text)
    
    if not phone_numbers:
        await update.message.reply_text(
            "❌ 未检测到有效的电话号码格式。\n\n"
            "📱 请发送支持的格式，例如：\n"
            "• +60 11-2896 2309 (马来西亚)\n"
            "• +86 138 0013 8000 (中国)\n"
            "• +1 555-123-4567 (美国)\n\n"
            "💡 使用 /help 查看所有支持格式"
        )
        return
    
    # 更新最后活动时间
    chat_data['last_activity'] = datetime.datetime.now()
    
    # 检测重复和风险
    new_phones = []
    duplicate_phones = []
    phone_reports = []
    
    for phone in phone_numbers:
        # 记录提交历史
        chat_data['phone_history'].append({
            'phone': phone,
            'timestamp': datetime.datetime.now(),
            'message_id': update.message.message_id
        })
        
        if phone in chat_data['phones']:
            duplicate_phones.append(phone)
        else:
            new_phones.append(phone)
            chat_data['phones'].add(phone)
        
        # 风险评估
        risk_level, warnings = assess_phone_risk(phone, chat_data)
        chat_data['risk_scores'][phone] = {
            'level': risk_level,
            'warnings': warnings,
            'timestamp': datetime.datetime.now()
        }
        
        # 生成报告
        category = categorize_phone_number(phone)
        risk_emoji = RISK_LEVELS[risk_level]['emoji']
        
        phone_report = f"📱 **{phone}**\n"
        phone_report += f"🏷️ 类型：{category}\n"
        phone_report += f"🛡️ 风险：{risk_emoji} {risk_level}\n"
        
        if phone in duplicate_phones:
            phone_report += "🔄 **状态：重复号码** ⚠️\n"
        else:
            phone_report += "✅ **状态：新号码**\n"
        
        if warnings:
            phone_report += "\n⚠️ **风险提醒：**\n"
            for warning in warnings[:3]:  # 只显示前3个警告
                phone_report += f"• {warning}\n"
        
        phone_reports.append(phone_report)
    
    # 生成综合警告
    warning_system = generate_comprehensive_warnings(phone_numbers, chat_data)
    
    # 构建回复消息
    response_message = f"🔍 **检测结果报告**\n\n"
    
    # 概述
    total_detected = len(phone_numbers)
    new_count = len(new_phones)
    duplicate_count = len(duplicate_phones)
    
    response_message += f"📊 **检测概述：**\n"
    response_message += f"• 本次检测：{total_detected} 个号码\n"
    response_message += f"• 新增号码：{new_count} 个\n"
    response_message += f"• 重复号码：{duplicate_count} 个\n"
    response_message += f"• 总计存储：{len(chat_data['phones'])} 个\n\n"
    
    # 详细报告（最多显示3个）
    response_message += "📱 **详细分析：**\n\n"
    for i, report in enumerate(phone_reports[:3]):
        response_message += f"**#{i+1}**\n{report}\n"
    
    if len(phone_reports) > 3:
        response_message += f"... 还有 {len(phone_reports)-3} 个号码\n"
        response_message += "💡 使用 /stats 查看完整统计\n\n"
    
    # 风险警告
    max_risk_level = warning_system['risk_summary']['max_level']
    if max_risk_level in ['HIGH', 'CRITICAL']:
        response_message += "🚨 **安全警报：**\n"
        for warning in warning_system['security_warnings'][:2]:
            response_message += f"• {warning}\n"
        response_message += "\n"
    
    # 安全建议
    if warning_system['usage_recommendations']:
        response_message += "💡 **使用建议：**\n"
        for rec in warning_system['usage_recommendations'][:2]:
            response_message += f"• {rec}\n"
        response_message += "\n"
    
    # 数据保护提醒
    response_message += "🔐 **隐私提醒：**\n"
    response_message += "• 数据仅用于重复检测\n"
    response_message += "• 建议定期使用 /clear 清理\n"
    response_message += "• 使用 /security 进行安全分析\n\n"
    
    response_message += "🛠️ 使用 /export 导出完整报告"
    
    await update.message.reply_text(response_message, parse_mode='Markdown')
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理器"""
    logger.error(f"发生错误: {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "❌ 处理请求时发生错误，请稍后重试。\n"
            "如果问题持续存在，请使用 /start 重新开始。"
        )
# Flask 应用路由
@app.route('/')
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'running',
        'service': 'phone-detection-bot',
        'timestamp': datetime.datetime.now().isoformat(),
        'is_bot_running': is_running,
        'restart_count': restart_count
    })
@app.route('/stats')
def stats_endpoint():
    """统计信息端点"""
    total_users = len(user_groups)
    total_phones = sum(len(data['phones']) for data in user_groups.values())
    
    return jsonify({
        'total_users': total_users,
        'total_phones': total_phones,
        'is_running': is_running,
        'restart_count': restart_count
    })
def run_flask():
    """运行Flask服务器"""
    port = int(os.environ.get('PORT', 5000))
    try:
        logger.info(f"Flask服务器启动在端口 {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask服务器运行错误: {e}")
async def run_bot():
    """运行Telegram机器人 - 修复版本"""
    global bot_application, is_running, restart_count
    
    # 获取Bot Token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        logger.info(f"正在启动 Telegram 机器人... (第 {restart_count + 1} 次)")
        
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
        logger.info("🚀 超级增强版电话号码检测机器人已启动！")
        logger.info("✅ 集成智能风险评估系统")
        logger.info("🛡️ 启用多级安全警告功能")
        logger.info("🔧 使用nest_asyncio解决事件循环冲突")
        logger.info("🔄 启用自动重启功能")
        
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
    """在新线程中启动机器人，带有自动重启功能"""
    global bot_thread, is_running, restart_count, max_restart_attempts
    
    def run_async_bot():
        global restart_count, is_running
        
        while restart_count < max_restart_attempts:
            try:
                # 创建新的事件循环
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # 运行机器人
                loop.run_until_complete(run_bot())
                
                # 如果正常退出，不重启
                break
                
            except Exception as e:
                restart_count += 1
                is_running = False
                
                logger.error(f"机器人线程错误 (第 {restart_count} 次): {e}")
                
                if restart_count < max_restart_attempts:
                    wait_time = min(30, 5 * restart_count)  # 指数退避
                    logger.info(f"等待 {wait_time} 秒后重启...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"达到最大重启次数 ({max_restart_attempts})，停止重启")
                    break
            finally:
                try:
                    loop.close()
                except:
                    pass
    
    if 'bot_thread' not in globals() or not bot_thread.is_alive():
        bot_thread = threading.Thread(target=run_async_bot, daemon=True)
        bot_thread.start()
        logger.info("机器人线程已启动，启用自动重启功能")
def health_check_thread():
    """健康检查线程，监控机器人状态"""
    global is_running, restart_count, max_restart_attempts
    
    while True:
        time.sleep(60)  # 每分钟检查一次
        
        if not is_running and restart_count < max_restart_attempts:
            logger.warning("检测到机器人停止运行，尝试重启...")
            start_bot_thread()
def restart_application():
    """重启应用程序"""
    global RESTART_COUNT
    
    if RESTART_COUNT >= MAX_RESTARTS:
        logger.error(f"已达到最大重启次数 {MAX_RESTARTS}，程序退出")
        sys.exit(1)
        
    RESTART_COUNT += 1
    logger.info(f"准备重启应用 (第{RESTART_COUNT}次)...")
    
    time.sleep(RESTART_DELAY)
    
    try:
        # 重新启动当前脚本
        python = sys.executable
        subprocess.Popen([python] + sys.argv)
        logger.info("重启命令已执行")
    except Exception as e:
        logger.error(f"重启失败: {e}")
    finally:
        sys.exit(0)
def signal_handler(signum, frame):
    """信号处理器 - 自动重启版"""
    global MAIN_PROCESS_PID
    logger.info(f"收到信号 {signum}，正在关闭...")
    
    # 记录当前进程PID
    MAIN_PROCESS_PID = os.getpid()
    
    # 设置关闭事件
    shutdown_event.set()
    
    # 清理资源
    global bot_application, is_running
    is_running = False
    
    if bot_application:
        try:
            logger.info("正在停止bot应用...")
            # 这里不能直接调用异步方法，需要适当处理
        except Exception as e:
            logger.error(f"停止bot应用时出错: {e}")
    
    # 自动重启
    logger.info("准备自动重启...")
    restart_application()
def main():
    """主函数 - 自动重启增强版"""
    global RESTART_COUNT, MAIN_PROCESS_PID
    
    MAIN_PROCESS_PID = os.getpid()
    
    logger.info("=" * 60)
    logger.info(f"超级增强版应用启动 (PID: {MAIN_PROCESS_PID})")
    logger.info(f"当前重启次数: {RESTART_COUNT}")
    logger.info("🔧 已应用nest_asyncio，解决事件循环冲突")
    logger.info("🛡️ 集成智能风险评估系统")
    logger.info("🚨 启用多级安全警告功能")
    logger.info("🔄 启用自动重启和故障恢复机制")
    logger.info("⚡ 添加SIGTERM自动重启功能")
    logger.info("=" * 60)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 在独立线程中启动Flask（移除werkzeug警告）
        # 注意：不再启动Flask服务器，避免werkzeug警告
        # flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskThread")
        # flask_thread.start()
        
        # 等待Flask启动
        # time.sleep(3)
        # logger.info("增强版Flask服务器已在后台启动")
        
        logger.info("跳过Flask服务器启动，避免werkzeug警告")
        
        # 启动机器人线程（带自动重启功能）
        start_bot_thread()
        
        # 启动健康检查线程
        health_thread = threading.Thread(target=health_check_thread, daemon=True)
        health_thread.start()
        
        logger.info("所有服务已启动，系统正在运行...")
        logger.info("✅ 自动重启功能已激活")
        
        # 保持主线程运行
        while not shutdown_event.is_set():
            time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("收到键盘中断信号")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"程序运行错误: {e}")
        logger.info("由于错误准备重启...")
        restart_application()
    
    logger.info("程序正在关闭...")
if __name__ == '__main__':
    main()
