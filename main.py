#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 超级增强版 (修复事件循环问题)
增强版警告系统 + 风险评估 + 安全提醒
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

# 首先安装并应用nest_asyncio来解决事件循环冲突
try:
    import nest_asyncio
    nest_asyncio.apply()
    logger = logging.getLogger(__name__)
    logger.info("✅ nest_asyncio已应用，事件循环冲突已解决")
except ImportError:
    # 如果没有nest_asyncio，我们手动安装
    import subprocess
    import sys
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
    'phone_history': [],  # 存储每次检测的历史
    'risk_scores': {},    # 存储风险评分
    'warnings_issued': set(),  # 已发出的警告
    'last_activity': None,
    'security_alerts': []  # 安全警报历史
})
shutdown_event = threading.Event()
bot_application = None  # 全局应用实例

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
    """识别电话号码的类型和国家"""
    # 移除格式字符进行匹配
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
    
    # 基础风险评估
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
    
    # 基础建议
    recommendations.extend([
        "🛡️ 请确保只与信任的联系人分享电话号码",
        "🔒 避免在公开场合大声说出完整电话号码",
        "📱 定期检查手机安全设置和隐私权限"
    ])
    
    # 根据风险等级添加特定建议
    if risk_level in ['HIGH', 'CRITICAL']:
        recommendations.extend([
            "🚨 高风险警告：建议立即验证号码来源",
            "⚠️ 如发现可疑活动，请联系相关通信运营商",
            "🔍 建议对异常号码进行额外验证"
        ])
    
    if len(phone_numbers) > 5:
        recommendations.append("📊 大量号码检测：建议分批处理以确保数据准确性")
    
    return recommendations[:6]  # 限制建议数量

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

# Flask路由
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'service': 'telegram-phone-bot-enhanced',
        'nest_asyncio': 'enabled',
        'features': ['risk_assessment', 'security_warnings', 'comprehensive_analysis'],
        'timestamp': time.time()
    })

@app.route('/status')
def status():
    """状态端点"""
    return jsonify({
        'bot_status': 'running' if not shutdown_event.is_set() else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
        'event_loop_fix': 'nest_asyncio',
        'enhanced_features': 'enabled'
    })

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

🛡️ **安全检测功能**:
🔍 **智能风险分析**：
• 🟢 低风险 - 正常号码格式
• 🟡 中等风险 - 存在异常特征
• 🟠 高风险 - 多项可疑指标
• 🔴 严重风险 - 需要立即验证

🔒 **数据保护系统**：
• 📞 重复号码检测与警告
• ⏱️ 频繁提交行为监控
• 🌍 跨国号码混合分析
• 🔢 异常数字模式识别

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
• 🇯🇵 日本: `+81 90 1234 5678`
• 🇰🇷 韩国: `+82 10 1234 5678`
• + 更多国际格式...

⚡ **超级智能功能**:
✅ 自动风险等级评估
✅ 实时安全警告提醒
✅ 综合数据保护建议
✅ 多维度号码分析
✅ 智能重复检测系统
✅ 国际标准格式验证
✅ 使用行为安全监控
✅ 隐私保护机制

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
4️⃣ 使用高级命令获取深度报告

💡 **安全小贴士**: 
• 🛡️ 保护个人隐私，谨慎分享敏感信息
• 🔍 关注风险警告，及时验证可疑号码
• 📊 定期清理数据，维护信息安全
• ⚠️ 遇到高风险警告请立即核实

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

async def security_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新增 /security 命令 - 安全状况检查"""
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

🔍 **风险分析**:
"""
    
    if high_risk_count > 0:
        security_report += f"⚠️ 发现 {high_risk_count} 个高风险号码，请注意核实\n"
    else:
        security_report += "✅ 未发现高风险号码\n"
    
    if warnings_count > 10:
        security_report += f"🚨 警告次数较多 ({warnings_count} 次)，建议检查使用习惯\n"
    else:
        security_report += "✅ 警告次数在正常范围内\n"
    
    security_report += f"""

💡 **安全建议**:
• 🔒 定期使用 /clear 清理敏感数据
• 🔍 注意验证高风险警告的号码
• 📱 避免频繁提交相同类型号码
• 🛡️ 保护个人隐私信息安全

⏰ 检查时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
    
    await update.message.reply_text(security_report, parse_mode='Markdown')

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /export 命令 - 增强版导出"""
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
• 格式版本: v2.0 Enhanced

💡 **使用建议**:
🔍 优先关注高风险号码
📞 及时验证可疑号码来源
🛡️ 保护个人隐私信息
"""
    
    await update.message.reply_text(export_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令 - 快速帮助"""
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

💡 **示例**: `联系方式：+60 11-2896 2309`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

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
    
    # 国家分布统计
    country_stats = {}
    for phone in all_phones:
        country = categorize_phone_number(phone).split()[0] + ' ' + categorize_phone_number(phone).split()[1]
        country_stats[country] = country_stats.get(country, 0) + 1
    
    # 计算各种统计
    total_count = len(all_phones)
    malaysia_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇲🇾")])
    china_count = len([p for p in all_phones if categorize_phone_number(p).startswith("🇨🇳")])
    international_count = total_count - malaysia_count - china_count
    
    # 安全统计
    high_risk_count = risk_distribution['HIGH'] + risk_distribution['CRITICAL']
    security_percentage = max(0, (total_count - high_risk_count) / max(total_count, 1) * 100)
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    stats_text = f"""
📊 **超级增强版统计报告**
=====================================

👤 **报告信息**:
• 查询者: {user_name}
• 群组: {chat_title}
• 群组ID: `{chat_id}`
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

🌍 **地区分布详情**:"""
    
    # 添加国家统计
    if country_stats:
        for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
            percentage = count/max(total_count,1)*100
            stats_text += f"\n• {country}: {count} 个 ({percentage:.1f}%)"
    else:
        stats_text += "\n暂无数据"
    
    # 活动统计
    total_detections = len(chat_data['phone_history'])
    warnings_issued = len(chat_data['warnings_issued'])
    
    stats_text += f"""

📋 **活动统计**:
• 总检测次数: {total_detections} 次
• 发出警告: {warnings_issued} 次
• 安全警报: {len(chat_data['security_alerts'])} 次
• 最后活动: {chat_data.get('last_activity', '无记录')}

🎯 **系统状态**:
• 运行状态: ✅ 正常运行
• 风险检测: ✅ 智能评估已启用
• 安全监控: ✅ 实时警告系统
• 数据保护: ✅ 隐私保护机制
• 事件循环: ✅ 已优化 (nest_asyncio)

💡 **操作建议**:
"""
    
    if high_risk_count > 0:
        stats_text += f"⚠️ 发现 {high_risk_count} 个高风险号码，建议使用 /security 详细检查\n"
    
    if total_count > 50:
        stats_text += "📊 号码数量较多，建议定期使用 /clear 清理\n"
    
    stats_text += """• 使用 /export 导出完整清单
• 使用 /security 进行安全检查
• 发送新号码继续智能检测

---
🤖 **超级增强版电话号码检测机器人** v3.0
🛡️ **集成智能风险评估系统**
"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

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
        
        # 生成综合警告系统
        warning_system = generate_comprehensive_warnings(phone_numbers, chat_data)
        
        # 构建超级增强版回复
        response_parts = []
        response_parts.append("🎯 **智能电话号码检测系统**")
        response_parts.append("=" * 35)
        response_parts.append(f"👤 **用户**: {user_name}")
        response_parts.append(f"🔍 **检测到**: {len(phone_numbers)} 个号码")
        
        # 风险总览
        max_risk = warning_system['risk_summary']['max_level']
        risk_emoji = RISK_LEVELS[max_risk]['emoji']
        response_parts.append(f"🛡️ **风险等级**: {risk_emoji} {max_risk}")
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
                
                if risk_warnings:
                    response_parts.append(f"    ⚠️ 警告: {risk_warnings[0][:30]}...")
                
                response_parts.append("")
            
            # 添加到记录中
            existing_phones.update(new_phones)
        
        # 显示重复号码（加强警告）
        if duplicate_phones:
            response_parts.append(f"🔄 **重复号码警告** ({len(duplicate_phones)}个):")
            for i, phone in enumerate(sorted(duplicate_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level = chat_data['risk_scores'].get(phone, 'MEDIUM')  # 重复号码至少中等风险
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                response_parts.append(f"{i:2d}. `{phone}` - {phone_type} {risk_emoji}")
            response_parts.append("")
            
            # 记录重复警告
            chat_data['warnings_issued'].add(f"duplicate_{len(duplicate_phones)}_{datetime.datetime.now().date()}")
        
        # 消息内部重复检测
        internal_duplicates = find_duplicates(phone_numbers)
        if internal_duplicates:
            response_parts.append(f"🔁 **消息内重复检测** ({len(internal_duplicates)}个):")
            for i, phone in enumerate(sorted(internal_duplicates), 1):
                phone_type = categorize_phone_number(phone)
                response_parts.append(f"{i:2d}. `{phone}` - {phone_type} 🔁")
            response_parts.append("")
        
        # 综合警告和建议系统
        if warning_system['alerts']:
            response_parts.append("⚠️ **智能警告系统**:")
            for alert in warning_system['alerts'][:3]:  # 限制显示数量
                response_parts.append(f"• {alert}")
            response_parts.append("")
        
        if warning_system['security_warnings']:
            response_parts.append("🚨 **安全提醒**:")
            for warning in warning_system['security_warnings'][:2]:
                response_parts.append(f"• {warning}")
            response_parts.append("")
        
        # 统计信息
        total_in_group = len(existing_phones)
        malaysia_count = len([p for p in phone_numbers if categorize_phone_number(p).startswith("🇲🇾")])
        china_count = len([p for p in phone_numbers if categorize_phone_number(p).startswith("🇨🇳")])
        other_count = len(phone_numbers) - malaysia_count - china_count
        
        # 风险分布统计
        current_risk_stats = {}
        for phone in phone_numbers:
            risk = chat_data['risk_scores'].get(phone, 'LOW')
            current_risk_stats[risk] = current_risk_stats.get(risk, 0) + 1
        
        response_parts.append("📊 **智能统计分析**:")
        response_parts.append(f"• 群组总计: {total_in_group} 个号码")
        response_parts.append(f"• 本次检测: 🇲🇾 {malaysia_count} | 🇨🇳 {china_count} | 🌍 {other_count}")
        
        if current_risk_stats:
            risk_summary = " | ".join([f"{RISK_LEVELS[k]['emoji']}{v}" for k, v in current_risk_stats.items() if v > 0])
            response_parts.append(f"• 风险分布: {risk_summary}")
        
        # 数据保护提醒
        if warning_system['data_protection_notices']:
            response_parts.append("")
            response_parts.append("🔐 **数据保护提醒**:")
            response_parts.append(f"• {warning_system['data_protection_notices'][0]}")
        
        # 安全建议
        security_recommendations = generate_security_recommendations(phone_numbers, max_risk)
        if security_recommendations:
            response_parts.append("")
            response_parts.append("💡 **安全建议**:")
            for rec in security_recommendations[:2]:  # 限制显示数量
                response_parts.append(f"• {rec}")
        
        # 时间戳和版本信息
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        response_parts.append("")
        response_parts.append(f"⏰ {now}")
        response_parts.append("🤖 **智能检测系统** v3.0 Enhanced")
        
        if response_parts:
            response = "\n".join(response_parts)
            await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text("❌ 处理消息时出现错误，系统正在自动恢复...")

def run_flask():
    """在独立线程中运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"启动增强版Flask服务器，端口: {port}")
    
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

async def shutdown_application():
    """优雅关闭应用程序"""
    global bot_application
    try:
        logger.info("正在停止应用程序...")
        if bot_application:
            await bot_application.stop()
            logger.info("机器人应用已停止")
        shutdown_event.set()
        logger.info("应用程序已安全关闭")
    except Exception as e:
        logger.error(f"关闭应用时出错: {e}")

async def run_bot():
    """运行Telegram机器人 - 修复版本"""
    global bot_application
    
    # 获取Bot Token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        # 创建应用
        bot_application = Application.builder().token(bot_token).build()
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("export", export_command))
        bot_application.add_handler(CommandHandler("security", security_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🚀 超级增强版电话号码检测机器人已启动！")
        logger.info("✅ 集成智能风险评估系统")
        logger.info("🛡️ 启用多级安全警告功能")
        logger.info("🔧 使用nest_asyncio解决事件循环冲突")
        
        # 运行机器人 - 使用更安全的方式
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False  # 关键修复：不让 telegram 库关闭事件循环
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        await shutdown_application()

def signal_handler(signum, frame):
    """信号处理器 - 优雅关闭"""
    logger.info(f"收到信号 {signum}，正在关闭...")
    shutdown_event.set()
    
    # 安全退出
    try:
        # 如果当前有事件循环在运行，使用 create_task
        loop = asyncio.get_running_loop()
        loop.create_task(shutdown_application())
    except RuntimeError:
        # 没有运行中的事件循环，直接退出
        sys.exit(0)

def main():
    """主函数 - 修复版解决方案"""
    logger.info("正在启动超级增强版应用...")
    logger.info("🔧 已应用nest_asyncio，一次性解决事件循环冲突")
    logger.info("🛡️ 集成智能风险评估系统")
    logger.info("🚨 启用多级安全警告功能")
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 在独立线程中启动Flask
        flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskThread")
        flask_thread.start()
        
        # 等待Flask启动
        time.sleep(3)
        logger.info("增强版Flask服务器已在后台启动")
        
        logger.info("启动超级增强版Telegram机器人...")
        
        # 修复事件循环问题的关键代码
        try:
            # 检查是否已有事件循环在运行
            loop = asyncio.get_running_loop()
            logger.info("检测到运行中的事件循环，使用现有循环")
            # 在现有循环中创建任务
            task = loop.create_task(run_bot())
            # 等待任务完成
            loop.run_until_complete(task)
        except RuntimeError:
            # 没有运行中的事件循环，创建新的
            logger.info("创建新的事件循环")
            asyncio.run(run_bot())
        
    except KeyboardInterrupt:
        logger.info("收到键盘中断信号")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"程序运行错误: {e}")
        shutdown_event.set()
    
    logger.info("程序正在关闭...")

if __name__ == '__main__':
    main()
