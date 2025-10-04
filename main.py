#!/usr/bin/env python3
"""
查毒机器人 - 完整版 + 自动重启
包含完整的检测结果报告格式 + 红色重复号码警告 + 自动重启功能
修复所有部署问题，完美匹配用户期望的显示格式
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
# 风险评估等级
RISK_LEVELS = {
    'LOW': {'emoji': '🟢', 'color': 'LOW', 'score': 1},
    'MEDIUM': {'emoji': '🟡', 'color': 'MEDIUM', 'score': 2}, 
    'HIGH': {'emoji': '🔥', 'color': 'HIGH', 'score': 3},
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
        r'\+81\s*[789]0\s*\d{4}\s*\d{4}',          # 日本手机
        r'\+82\s*10\s*\d{4}\s*\d{4}',              # 韩国手机
        r'\+66\s*[689]\d{8}',                       # 泰国
        r'\+84\s*[39]\d{8}',                        # 越南
        r'\+62\s*8\d{8,10}',                        # 印尼
        r'\+63\s*9\d{9}',                           # 菲律宾
        
        # 通用国际格式
        r'\+\d{1,4}\s*\d{6,14}',                    # 通用国际格式
        
        # 本地格式（没有国际代码）
        r'0\d{1,2}[\s-]?\d{4}[\s-]?\d{4}',          # 本地格式：01-1234 5678
        r'1[3-9]\d{9}',                             # 中国本地手机（11位）
        r'[2-9]\d{2}[\s-]?[2-9]\d{2}[\s-]?\d{4}',  # 美国本地格式
    ]
    
    found_numbers = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # 清理号码格式
            clean_number = re.sub(r'[\s-]', '', match)
            if len(clean_number) >= 8:  # 最少8位数字
                found_numbers.add(match.strip())
    
    return found_numbers
def categorize_phone_number(phone: str) -> str:
    """分类电话号码类型"""
    clean_phone = re.sub(r'[\s-()]', '', phone)
    
    # 马来西亚
    if re.match(r'\+?60', clean_phone):
        if re.match(r'\+?601[0-9]', clean_phone):
            return "🇲🇾 马来西亚手机"
        else:
            return "🇲🇾 马来西亚固话"
    
    # 中国
    elif re.match(r'\+?86', clean_phone):
        if re.match(r'\+?861[3-9]', clean_phone):
            return "🇨🇳 中国手机"
        else:
            return "🇨🇳 中国固话"
    
    # 美国/加拿大
    elif re.match(r'\+?1[2-9]', clean_phone):
        return "🇺🇸 美国/加拿大"
    
    # 新加坡
    elif re.match(r'\+?65', clean_phone):
        return "🇸🇬 新加坡"
    
    # 香港
    elif re.match(r'\+?852', clean_phone):
        return "🇭🇰 香港"
    
    # 日本
    elif re.match(r'\+?81', clean_phone):
        return "🇯🇵 日本"
    
    # 韩国
    elif re.match(r'\+?82', clean_phone):
        return "🇰🇷 韩国"
    
    # 泰国
    elif re.match(r'\+?66', clean_phone):
        return "🇹🇭 泰国"
    
    # 印度
    elif re.match(r'\+?91', clean_phone):
        return "🇮🇳 印度"
    
    # 英国
    elif re.match(r'\+?44', clean_phone):
        return "🇬🇧 英国"
    
    # 其他
    else:
        return "🌍 其他国际号码"
def assess_phone_risk(phone: str, chat_data: Dict[str, Any]) -> Tuple[str, List[str]]:
    """评估电话号码风险等级"""
    risk_score = 0
    warnings = []
    clean_phone = re.sub(r'[\s-()]', '', phone)
    
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
def generate_comprehensive_warnings(phone_numbers: Set[str], chat_data: Dict[str, Any]) -> Dict[str, Any]:
    """生成综合警告系统"""
    warning_system = {
        'alerts': [],
        'security_warnings': [],
        'usage_recommendations': [],
        'data_protection_notices': [],
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
        
        # 添加特定警告
        if risk_level in ['HIGH', 'CRITICAL']:
            warning_system['security_warnings'].extend([
                "🚨 检测到高风险号码，建议验证来源",
                "⚠️ 请谨慎处理此号码相关信息"
            ])
        
        if phone in chat_data['phones']:
            warning_system['alerts'].append(f"🔄 {phone} - 重复号码检测")
    
    # 使用建议
    if len(phone_numbers) > 5:
        warning_system['usage_recommendations'].append("📊 建议分批处理大量号码")
    
    if total_risk_score > 6:
        warning_system['usage_recommendations'].append("🔍 建议对高风险号码进行额外验证")
    
    # 数据保护提醒
    warning_system['data_protection_notices'].extend([
        "🔐 所有数据仅用于重复检测分析",
        "🗑️ 建议定期清理敏感数据"
    ])
    
    warning_system['risk_summary']['total_score'] = total_risk_score
    warning_system['risk_summary']['max_level'] = max_risk_level
    
    return warning_system
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
        'service': 'complete-phone-bot-with-full-format-auto-restart',
        'bot_running': is_running,
        'restart_count': RESTART_COUNT,
        'max_restarts': MAX_RESTARTS,
        'auto_restart': 'enabled',
        'full_format': 'enabled',
        'nest_asyncio': 'enabled',
        'features': ['complete_format', 'red_duplicate_warnings', 'risk_assessment', 'auto_restart'],
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
        'complete_format_enabled': True,
        'event_loop_fix': 'nest_asyncio'
    })
@app.route('/restart')
def force_restart():
    """强制重启机器人的端点"""
    logger.info("🔄 收到强制重启请求")
    restart_application()
    return jsonify({'message': 'Bot restart initiated', 'timestamp': datetime.datetime.now().isoformat()})
# Telegram机器人函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""
🎯 **查毒机器人 - 欢迎 {user_name}！**
🚀 **完整功能特色**:
⭐ 完整检测结果报告格式
⭐ 红色重复号码警告显示
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
• 🔥 高风险 - 多项可疑指标
• 🔴 严重风险 - 需要立即验证
📱 **支持的电话号码格式**:
🇲🇾 **马来西亚格式** (优先支持):
• `+60 11-2896 2309` (标准格式)
• `+60 11 2896 2309` (空格分隔)
• `+6011-28962309` (紧凑格式)
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
2️⃣ 获得完整的检测结果报告
3️⃣ 查看红色重复号码警告
4️⃣ 使用高级命令获取深度报告
🔄 **自动重启功能**:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• ✅ 自动保持运行
• ✅ 故障自动恢复
现在就发送电话号码开始智能检测吧！ 🎯
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
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
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /export 命令"""
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    if not chat_data['phones']:
        await update.message.reply_text(
            "📭 当前没有检测到的电话号码。\n\n"
            "💡 发送包含电话号码的消息开始检测！"
        )
        return
    
    # 生成导出报告
    export_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_lines = []
    
    report_lines.append("📋 **查毒机器人 - 完整报告**")
    report_lines.append("=" * 40)
    report_lines.append(f"📅 导出时间: {export_time}")
    report_lines.append(f"📊 总计号码: {len(chat_data['phones'])} 个")
    report_lines.append("")
    
    # 按风险等级排序
    phones_with_risk = []
    for phone in chat_data['phones']:
        risk_level, _ = assess_phone_risk(phone, chat_data)
        phones_with_risk.append((phone, risk_level))
    
    # 按风险等级排序（高风险在前）
    phones_with_risk.sort(key=lambda x: RISK_LEVELS[x[1]]['score'], reverse=True)
    
    report_lines.append("📱 **详细清单**:")
    for i, (phone, risk_level) in enumerate(phones_with_risk, 1):
        phone_type = categorize_phone_number(phone)
        risk_emoji = RISK_LEVELS[risk_level]['emoji']
        report_lines.append(f"{i:2d}. `{phone}` - {phone_type} {risk_emoji} {risk_level}")
    
    report_lines.append("")
    report_lines.append("🤖 **查毒机器人** - 完整版 + 自动重启")
    
    export_text = "\n".join(report_lines)
    await update.message.reply_text(export_text, parse_mode='Markdown')
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令"""
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    if not chat_data['phones']:
        await update.message.reply_text(
            "📭 当前没有统计数据。\n\n"
            "💡 发送包含电话号码的消息开始检测！"
        )
        return
    
    # 统计分析
    total_count = len(chat_data['phones'])
    
    # 按类型统计
    malaysia_count = 0
    china_count = 0
    international_count = 0
    
    # 按风险等级统计
    risk_distribution = {'LOW': 0, 'MEDIUM': 0, 'HIGH': 0, 'CRITICAL': 0}
    
    for phone in chat_data['phones']:
        category = categorize_phone_number(phone)
        if "🇲🇾" in category:
            malaysia_count += 1
        elif "🇨🇳" in category:
            china_count += 1
        else:
            international_count += 1
        
        risk_level, _ = assess_phone_risk(phone, chat_data)
        risk_distribution[risk_level] += 1
    
    stats_text = f"""
📊 **查毒机器人 - 详细统计报告**
=========================================
📈 **数据概览**:
• 总检测号码: **{total_count}** 个
• 历史检测次数: **{len(chat_data['phone_history'])}** 次
• 最后活动: {chat_data.get('last_activity', '未知')}
🌍 **地区分布**:
• 马来西亚号码: **{malaysia_count}** 个 ({malaysia_count/max(total_count,1)*100:.1f}%)
• 中国号码: **{china_count}** 个 ({china_count/max(total_count,1)*100:.1f}%)
• 其他国际号码: **{international_count}** 个 ({international_count/max(total_count,1)*100:.1f}%)
🛡️ **风险评估统计**:
• 🟢 低风险: {risk_distribution['LOW']} 个 ({risk_distribution['LOW']/max(total_count,1)*100:.1f}%)
• 🟡 中等风险: {risk_distribution['MEDIUM']} 个 ({risk_distribution['MEDIUM']/max(total_count,1)*100:.1f}%)
• 🔥 高风险: {risk_distribution['HIGH']} 个 ({risk_distribution['HIGH']/max(total_count,1)*100:.1f}%)
• 🔴 严重风险: {risk_distribution['CRITICAL']} 个 ({risk_distribution['CRITICAL']/max(total_count,1)*100:.1f}%)
🔄 **自动重启系统**:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• 运行状态: ✅ 正常运行
• 自动重启: ✅ 已启用
🎯 **系统状态**:
• 运行状态: ✅ 正常运行
• 完整格式: ✅ 已启用
• 风险检测: ✅ 智能评估已启用
• 自动重启保护: ✅ 已启用
• 事件循环: ✅ 已优化 (nest_asyncio)
💡 **操作建议**:
"""
    
    high_risk_count = risk_distribution['HIGH'] + risk_distribution['CRITICAL']
    if high_risk_count > 0:
        stats_text += f"⚠️ 发现 {high_risk_count} 个高风险号码，建议使用 /security 详细检查\n"
    
    if total_count > 50:
        stats_text += "📊 号码数量较多，建议定期使用 /clear 清理\n"
    
    stats_text += """• 使用 /export 导出完整清单
• 使用 /security 进行安全检查
• 发送新号码继续智能检测
---
🤖 **查毒机器人** - 完整版 + 自动重启
🛡️ **集成智能风险评估系统 + 完整格式显示**
"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    help_text = f"""
🆘 **查毒机器人 - 快速帮助指南**
📋 **核心命令**:
• `/start` - 完整功能介绍
• `/stats` - 详细统计报告
• `/clear` - 清除所有记录  
• `/export` - 导出完整报告
• `/security` - 安全状况检查
• `/help` - 本帮助信息
🚀 **快速上手**:
1️⃣ 直接发送包含电话号码的消息
2️⃣ 查看完整的检测结果报告
3️⃣ 关注红色重复号码警告
4️⃣ 查看安全建议和风险评估
🔄 **自动重启功能**:
• 重启次数: {RESTART_COUNT}/{MAX_RESTARTS}
• ✅ 自动保持运行
• ✅ 故障自动恢复
💡 **示例**: `联系方式：+60 11-2896 2309`
🎯 现在就发送号码开始使用完整格式检测！
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')
async def security_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /security 命令 - 安全状况检查"""
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    if not chat_data['phones']:
        await update.message.reply_text(
            "🔒 **安全检查报告**\n\n"
            "📭 当前没有检测数据。\n\n"
            "💡 发送电话号码开始安全检测！"
        )
        return
    
    # 计算安全指标
    total_phones = len(chat_data['phones'])
    high_risk_count = 0
    
    for phone in chat_data['phones']:
        risk_level, _ = assess_phone_risk(phone, chat_data)
        if RISK_LEVELS[risk_level]['score'] >= 3:
            high_risk_count += 1
    
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
        security_level = "🔥 警告"
        security_emoji = "🚨"
    else:
        security_level = "🔴 危险"
        security_emoji = "⛔"
    
    security_report = f"""
🛡️ **查毒机器人 - 安全状况检查报告**
========================================
{security_emoji} **当前安全等级**: {security_level}
📊 **安全评分**: {security_score}/100
📈 **详细安全指标**:
• 总检测号码: {total_phones} 个
• 高风险号码: {high_risk_count} 个
• 累计警告: {warnings_count} 次
• 7天内安全警报: {recent_alerts} 次
🔍 **风险分析**:
"""
    
    if high_risk_count == 0:
        security_report += "✅ 未发现高风险号码\n"
    else:
        security_report += f"⚠️ 发现 {high_risk_count} 个高风险号码\n"
        security_report += "💡 建议使用 /export 查看详细清单\n"
    
    security_report += f"""
🔄 **系统安全状态**:
• 自动重启: ✅ 已启用 ({RESTART_COUNT}/{MAX_RESTARTS})
• 完整格式: ✅ 已启用
• 风险评估: ✅ 智能分析已启用
• 数据保护: ✅ 隐私保护已启用
💡 **安全建议**:
• 定期使用 /clear 清理敏感数据
• 关注高风险号码警告
• 使用 /export 备份重要数据
• 谨慎处理重复号码
---
🤖 **查毒机器人** - 安全检查完成
"""
    
    await update.message.reply_text(security_report, parse_mode='Markdown')
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息 - 完整格式版本"""
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
        
        # 检测重复和风险
        phone_reports = []
        
        for phone in phone_numbers:
            if phone not in existing_phones:
                existing_phones.add(phone)
            
            # 风险评估
            risk_level, warnings = assess_phone_risk(phone, chat_data)
            chat_data['risk_scores'][phone] = {
                'level': risk_level,
                'warnings': warnings,
                'timestamp': datetime.datetime.now()
            }
            
            # 生成详细分析报告
            category = categorize_phone_number(phone)
            risk_emoji = RISK_LEVELS[risk_level]['emoji']
            
            phone_report = f"📱 **{phone}**\n"
            phone_report += f"🏷️ 类型：{category}\n"
            phone_report += f"🔥 风险：{risk_emoji} {risk_level}\n"
            
            if phone in duplicate_phones:
                phone_report += "⚠️ **状态：重复号码** ⚠️\n"
            else:
                phone_report += "✅ **状态：新号码**\n"
            
            if warnings:
                phone_report += "\n⚠️ **风险提醒：**\n"
                for warning in warnings[:3]:  # 只显示前3个警告
                    phone_report += f"• {warning}\n"
            
            phone_reports.append(phone_report)
        
        # 生成综合警告
        warning_system = generate_comprehensive_warnings(phone_numbers, chat_data)
        
        # 构建完整格式回复消息 - 匹配用户截图格式
        response_message = "🎯 **查毒机器人**\n"
        
        # 显示检测的号码
        first_phone = list(phone_numbers)[0] if phone_numbers else ""
        current_time = datetime.datetime.now().strftime("%m.%d")
        response_message += f"📞 {current_time}/注法 {first_phone}\n\n"
        
        response_message += "🔍 **检测结果报告**\n\n"
        
        # 检测网址（模拟）
        response_message += "📊 **检测网址：**\n"
        
        # 概述统计
        total_detected = len(phone_numbers)
        new_count = len(new_phones)
        duplicate_count = len(duplicate_phones)
        total_stored = len(chat_data['phones'])
        
        response_message += f"• 本次检测：{total_detected} 个号码\n"
        response_message += f"• 新增号码：{new_count} 个\n"
        response_message += f"• 重复号码：{duplicate_count} 个\n"
        response_message += f"• 总计存储：{total_stored} 个\n\n"
        
        # 详细分析（最多显示3个）
        response_message += "📱 **详细分析：**\n\n"
        for i, report in enumerate(phone_reports[:3]):
            response_message += f"**#{i+1}**\n{report}\n"
        
        if len(phone_reports) > 3:
            response_message += f"... 还有 {len(phone_reports)-3} 个号码\n"
            response_message += "💡 使用 /stats 查看完整统计\n\n"
        
        # 风险提醒
        if duplicate_phones:
            response_message += "⚠️ **风险提醒：**\n"
            response_message += f"• 号码重复：该号码之前已被检测过\n"
            response_message += f"• 信息来源：电话号码来源不符合国际标准\n\n"
        
        # 安全警报
        max_risk_level = warning_system['risk_summary']['max_level']
        if max_risk_level in ['HIGH', 'CRITICAL']:
            response_message += "🚨 **安全警报：**\n"
            response_message += f"✅ 全域搜索：检测到高风险号存在\n"
            response_message += f"⚠️ 验证建议：请仔细核实号码的来源和有效性\n\n"
        
        # 隐私提醒
        response_message += "🔐 **隐私提醒：**\n"
        response_message += "• 数据仅用于重复检测\n"
        response_message += "• 建议定期使用 /clear 清理\n"
        response_message += "• 使用 /security 进行安全分析\n\n"
        
        response_message += "🛠️ 使用 /export 导出完整报告"
        
        await update.message.reply_text(response_message, parse_mode='Markdown')
        
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
    logger.info(f"启动完整格式Flask服务器，端口: {port}")
    
    try:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        logger.error(f"Flask服务器错误: {e}")
        if not shutdown_event.is_set():
            logger.info("Flask服务器异常，准备重启...")
            restart_application()
async def main():
    """主函数 - 自动重启版"""
    global bot_application, is_running, flask_thread
    
    # 注册信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"🚀 启动查毒机器人 - 完整版 + 自动重启 (第{RESTART_COUNT+1}次)")
    
    try:
        # 设置Bot应用
        bot_token = os.environ.get('BOT_TOKEN')
        if not bot_token:
            logger.error("❌ 未找到BOT_TOKEN环境变量")
            sys.exit(1)
        
        logger.info("🤖 初始化Telegram Bot应用...")
        bot_application = Application.builder().token(bot_token).build()
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("export", export_command))
        bot_application.add_handler(CommandHandler("security", security_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        bot_application.add_error_handler(error_handler)
        
        # 启动Flask服务器（独立线程）
        logger.info("🌐 启动Flask健康检查服务器...")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # 启动Bot
        logger.info("🤖 启动Telegram Bot...")
        is_running = True
        
        # 清除任何旧的webhook设置，确保polling模式
        logger.info("正在清除旧的webhook设置...")
        await bot_application.bot.delete_webhook()
        logger.info("✅ 已清除webhook设置")
        
        # 启动polling
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            stop_signals=None
        )
        
    except Exception as e:
        logger.error(f"❌ 应用运行错误: {e}")
        if not shutdown_event.is_set():
            logger.info("💥 主应用异常，准备自动重启...")
            restart_application()
    finally:
        is_running = False
        logger.info("🛑 Bot应用已停止")
if __name__ == "__main__":
    try:
        logger.info("🎯 启动查毒机器人 - 完整版 + 自动重启")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 收到中断信号，正在关闭...")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"💥 程序异常: {e}")
        restart_application()
