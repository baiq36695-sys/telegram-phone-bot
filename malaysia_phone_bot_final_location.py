#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管号机器人终极版 v17.0 - 2025完整功能+自动重启版
包含所有高级功能 + Webhook + 安全性 + 自动重启
"""

import os
import re
import json
import threading
import time
import signal
import sys
import subprocess
import hashlib
import hmac
from datetime import datetime, timedelta
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import asyncio
import logging

# Flask和Telegram相关导入
try:
    from flask import Flask, request, jsonify
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    import requests
except ImportError as e:
    print(f"❌ 依赖库缺失: {e}")
    print("请运行: uv pip install flask python-telegram-bot requests")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 机器人配置
BOT_TOKEN = "8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU"
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'https://your-app-name.onrender.com')
PORT = int(os.environ.get('PORT', 10000))

# 马来西亚手机号码运营商和归属地 - 2025修正版
MALAYSIA_MOBILE_PREFIXES = {
    '010': 'DiGi',
    '011': 'DiGi', 
    '012': 'Maxis',
    '013': 'DiGi',
    '014': 'DiGi',
    '015': 'DiGi',
    '016': 'DiGi',
    '017': 'Maxis',
    '018': 'U Mobile',
    '019': 'DiGi',
    '020': 'Electcoms'
}

# 马来西亚固话区号和归属地 - 2025修正版
MALAYSIA_LANDLINE_CODES = {
    '03': '雪兰莪/吉隆坡/布城',
    '04': '吉打/槟城',
    '05': '霹雳',
    '06': '马六甲/森美兰',
    '07': '柔佛',
    '08': '沙巴',
    '09': '吉兰丹/登嘉楼',
    '082': '沙捞越古晋',
    '083': '沙捞越斯里阿曼',
    '084': '沙捞越沙拉卓',
    '085': '沙捞越美里',
    '086': '沙捞越泗里街',
    '087': '沙巴亚庇',
    '088': '沙巴斗湖',
    '089': '沙巴根地咬'
}

class PhoneNumberState:
    """线程安全的电话号码状态管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = datetime.now()
        self.heartbeat_count = 0
        self.last_heartbeat = None
        self.message_count = 0
        
        # 全局号码注册表
        self.phone_registry = {}
        
        # 用户数据
        self.user_data = defaultdict(lambda: {
            'first_seen': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'query_count': 0,
            'phone_numbers_found': 0,
            'queries_today': 0,
            'last_query_date': None,
            'phone_history': deque(maxlen=100),
            'hourly_stats': defaultdict(int),
            'carrier_stats': defaultdict(int),
            'daily_queries': defaultdict(int),
            'username': None,
            'first_name': None,
            'last_name': None
        })
        
        self.user_names = {}
        
        self.global_stats = {
            'total_queries': 0,
            'total_users': 0,
            'total_phone_numbers': 0,
            'total_duplicates': 0,
            'start_time': self.start_time.isoformat(),
            'hourly_distribution': defaultdict(int),
            'carrier_distribution': defaultdict(int),
            'daily_stats': defaultdict(int)
        }
        
        # 启动心跳线程
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_worker, daemon=True)
        self.heartbeat_thread.start()
        logger.info("✅ 管号机器人系统启动（v17.0-终极版）")

    def _heartbeat_worker(self):
        """心跳监控线程"""
        while True:
            try:
                with self._lock:
                    self.heartbeat_count += 1
                    self.last_heartbeat = datetime.now()
                time.sleep(300)
            except Exception as e:
                logger.error(f"心跳监控错误: {e}")
                time.sleep(60)

    def update_user_info(self, user_id, user_info):
        """更新用户信息"""
        with self._lock:
            username = user_info.get('username')
            first_name = user_info.get('first_name', '')
            last_name = user_info.get('last_name', '')
            
            self.user_data[user_id]['username'] = username
            self.user_data[user_id]['first_name'] = first_name
            self.user_data[user_id]['last_name'] = last_name
            
            # 创建显示名称（优先显示真实姓名）
            if first_name or last_name:
                display_name = f"{first_name} {last_name}".strip()
            elif username:
                display_name = username
            else:
                display_name = f"用户{user_id}"
            
            self.user_names[user_id] = display_name

    def get_user_display_name(self, user_id):
        """获取用户显示名称"""
        user_data = self.user_data[user_id]
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        
        if first_name or last_name:
            return f"{first_name} {last_name}".strip()
        elif user_data.get('username'):
            return user_data.get('username')
        else:
            return f"用户{user_id}"

    def register_phone_number(self, phone_number, user_id, user_info=None):
        """注册电话号码并检查重复"""
        with self._lock:
            normalized_phone = self._normalize_phone(phone_number)
            current_time = datetime.now()
            current_user_name = self.get_user_display_name(user_id)
            
            if normalized_phone in self.phone_registry:
                registry_entry = self.phone_registry[normalized_phone]
                registry_entry['count'] += 1
                registry_entry['users'].add(user_id)
                self.global_stats['total_duplicates'] += 1
                
                return {
                    'is_duplicate': True,
                    'first_seen': registry_entry['first_seen'],
                    'occurrence_count': registry_entry['count'],
                    'total_users': len(registry_entry['users']),
                    'first_user_name': registry_entry['first_user_name'],
                    'first_user_id': registry_entry['first_user'],
                    'current_user_name': current_user_name,
                    'formatted_phone': self._format_phone_display(normalized_phone)
                }
            else:
                self.phone_registry[normalized_phone] = {
                    'first_seen': current_time,
                    'count': 1,
                    'users': {user_id},
                    'first_user': user_id,
                    'first_user_name': current_user_name,
                    'first_user_data': user_info or {}
                }
                
                return {
                    'is_duplicate': False,
                    'first_seen': current_time,
                    'occurrence_count': 1,
                    'total_users': 1,
                    'first_user_name': current_user_name,
                    'first_user_id': user_id,
                    'current_user_name': current_user_name,
                    'formatted_phone': self._format_phone_display(normalized_phone)
                }

    def clear_all_data(self):
        """清理所有数据"""
        with self._lock:
            self.phone_registry.clear()
            self.user_data.clear()
            self.user_names.clear()
            
            self.global_stats.update({
                'total_queries': 0,
                'total_users': 0,
                'total_phone_numbers': 0,
                'total_duplicates': 0,
                'hourly_distribution': defaultdict(int),
                'carrier_distribution': defaultdict(int),
                'daily_stats': defaultdict(int)
            })
            
            logger.info("🗑️ 所有数据已清理")
            return True

    def _normalize_phone(self, phone):
        """标准化电话号码格式"""
        clean = re.sub(r'[^\d]', '', phone)
        if clean.startswith('60'):
            return clean
        elif clean.startswith('0'):
            return '60' + clean[1:]
        else:
            return '60' + clean

    def _format_phone_display(self, normalized_phone):
        """格式化电话号码用于显示"""
        if normalized_phone.startswith('60') and len(normalized_phone) >= 11:
            local_number = normalized_phone[2:]
            if len(local_number) >= 9:
                if len(local_number) == 9:
                    return f"+60 {local_number[:2]}-{local_number[2:5]} {local_number[5:]}"
                elif len(local_number) == 10:
                    return f"+60 {local_number[:3]}-{local_number[3:6]} {local_number[6:]}"
                else:
                    return f"+60 {local_number}"
        return normalized_phone

    def record_query(self, user_id, phone_numbers_found=0, carriers=None):
        """记录查询统计"""
        try:
            with self._lock:
                current_time = datetime.now()
                today = current_time.date().isoformat()
                hour = current_time.hour
                
                user_data = self.user_data[user_id]
                user_data['last_seen'] = current_time.isoformat()
                user_data['query_count'] += 1
                user_data['phone_numbers_found'] += phone_numbers_found
                
                if user_data['last_query_date'] != today:
                    user_data['queries_today'] = 0
                    user_data['last_query_date'] = today
                
                user_data['queries_today'] += 1
                user_data['hourly_stats'][hour] += 1
                user_data['daily_queries'][today] += 1
                
                if carriers:
                    for carrier in carriers:
                        user_data['carrier_stats'][carrier] += 1
                        self.global_stats['carrier_distribution'][carrier] += 1
                
                self.global_stats['total_queries'] += 1
                self.global_stats['total_phone_numbers'] += phone_numbers_found
                self.global_stats['hourly_distribution'][hour] += 1
                self.global_stats['daily_stats'][today] += 1
                self.global_stats['total_users'] = len(self.user_data)
                
                self.message_count += 1
        except Exception as e:
            logger.error(f"记录查询统计错误: {e}")

    def get_user_stats(self, user_id):
        """获取用户统计"""
        with self._lock:
            return dict(self.user_data[user_id])

    def get_global_stats(self):
        """获取全局统计"""
        with self._lock:
            stats = dict(self.global_stats)
            stats['total_registered_phones'] = len(self.phone_registry)
            return stats

    def get_system_status(self):
        """获取系统状态"""
        with self._lock:
            uptime = datetime.now() - self.start_time
            return {
                'uptime': str(uptime),
                'heartbeat_count': self.heartbeat_count,
                'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
                'message_count': self.message_count,
                'active_users': len(self.user_data),
                'registered_phones': len(self.phone_registry)
            }

# 全局状态管理
phone_state = PhoneNumberState()

def clean_malaysia_phone_number(text):
    """从文本中提取马来西亚电话号码"""
    try:
        patterns = [
            r'\+60\s*[1-9]\d{1,2}[-\s]*\d{3,4}[-\s]*\d{3,4}',
            r'60[1-9]\d{1,2}\d{7,9}',
            r'0[1-9]\d{1,2}[-\s]*\d{3,4}[-\s]*\d{3,4}',
            r'[1-9]\d{8,10}'
        ]
        
        phone_numbers = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            phone_numbers.extend(matches)
        
        cleaned_phones = []
        for phone in phone_numbers:
            cleaned = re.sub(r'[^\d]', '', phone)
            if 8 <= len(cleaned) <= 12:
                cleaned_phones.append(phone)
        
        return list(set(cleaned_phones))
    except Exception as e:
        logger.error(f"提取电话号码错误: {e}")
        return []

def analyze_malaysia_phone(phone_number):
    """分析马来西亚电话号码"""
    analysis = {
        'original': phone_number,
        'is_valid': False,
        'type': '未知',
        'location': '未知归属地',
        'carrier': '未知',
        'formatted': phone_number
    }
    
    try:
        clean_number = re.sub(r'[^\d]', '', phone_number)
        
        if clean_number.startswith('60'):
            local_number = clean_number[2:]
        elif clean_number.startswith('0'):
            local_number = clean_number[1:]
        else:
            local_number = clean_number
        
        if len(local_number) >= 9:
            prefix_3 = local_number[:3]
            prefix_2 = local_number[:2]
            
            # 检查手机号码
            if prefix_3 in MALAYSIA_MOBILE_PREFIXES:
                analysis['is_valid'] = True
                analysis['type'] = '手机'
                analysis['carrier'] = MALAYSIA_MOBILE_PREFIXES[prefix_3]
                analysis['location'] = f"📱 {analysis['carrier']}·全马来西亚"
                analysis['formatted'] = f"+60 {prefix_3}-{local_number[3:6]}-{local_number[6:]}"
            
            # 检查固话
            elif prefix_3 in MALAYSIA_LANDLINE_CODES:
                analysis['is_valid'] = True
                analysis['type'] = '固话'
                analysis['carrier'] = '固话'
                analysis['location'] = f"🏠 {MALAYSIA_LANDLINE_CODES[prefix_3]}"
                analysis['formatted'] = f"+60 {prefix_3}-{local_number[3:6]}-{local_number[6:]}"
            
            elif prefix_2 in MALAYSIA_LANDLINE_CODES:
                analysis['is_valid'] = True
                analysis['type'] = '固话'
                analysis['carrier'] = '固话'
                analysis['location'] = f"🏠 {MALAYSIA_LANDLINE_CODES[prefix_2]}"
                analysis['formatted'] = f"+60 {prefix_2}-{local_number[2:6]}-{local_number[6:]}"
        
        if analysis['location'] == '未知归属地' and 8 <= len(local_number) <= 11:
            analysis['location'] = '🇲🇾 马来西亚·未知运营商'
            analysis['is_valid'] = True
            analysis['carrier'] = '未知运营商'
    
    except Exception as e:
        logger.error(f"马来西亚电话号码分析错误: {e}")
    
    return analysis

# Flask应用和Telegram设置
app = Flask(__name__)
application = None
executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="telegram-worker")

# 频率限制
request_times = {}
RATE_LIMIT = 15  # 每分钟最多15个请求

def is_rate_limited(user_id):
    """检查是否超过频率限制"""
    now = time.time()
    user_requests = request_times.get(user_id, [])
    
    # 清理1分钟前的记录
    user_requests = [req_time for req_time in user_requests if now - req_time < 60]
    
    if len(user_requests) >= RATE_LIMIT:
        return True
    
    user_requests.append(now)
    request_times[user_id] = user_requests
    return False

# Telegram命令处理函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_state.record_query(user_id)
    
    welcome_text = f"""🗣️ **欢迎使用管号机器人!** [v17.0-终极版 🚀]

🔍 **专业功能:**
• 📱 马来西亚手机和固话识别  
• ⏰ 首次出现时间记录
• 🔄 重复号码检测及关联信息
• 👥 用户追踪和统计
• 📍 **精准归属地显示（已修复！）**
• 🔒 **安全防护和自动重启**

📱 **支持的马来西亚号码格式:**
```
+60 11-6852 8782  (国际格式)
011-6852 8782     (本地手机)
03-1234 5678     (固话)
60116852782      (纯数字)
```

🚀 **使用方法:**
直接发送马来西亚电话号码开始检测!

💡 输入 /help 查看更多命令。
🔥 **新功能:** 现在显示详细的运营商信息（Maxis、DiGi、U Mobile等）！

⚠️ **2025年10月更新:** 归属地显示功能已完全修复！"""

    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_state.record_query(user_id)
    
    help_text = """🗣️ **管号机器人 - 帮助** [v17.0-终极版 🚀]

🔍 **主要功能:**
• 检测马来西亚手机和固话号码
• 记录首次出现时间
• 检测重复号码及关联信息
• 用户追踪和统计
• **显示精准归属地 📍（已修复）**
• **自动重启和故障恢复**

📱 **支持格式:**
• +60 11-6852 8782（国际格式）
• 011-6852 8782（本地手机）
• 03-1234 5678（固话）
• 60116852782（纯数字）

⚡ **快速命令:**
• /start - 开始使用
• /help - 显示帮助
• /stats - 查看个人统计
• /status - 系统状态
• /clear - 清理所有数据 🗑️

💡 **使用方法:**
直接发送包含马来西亚电话号码的消息即可自动检测和分析!

🔥 **最新功能:** 
• 详细运营商显示（📱 Maxis·全马来西亚）
• 固话归属地显示（🏠 雪兰莪/吉隆坡/布城）
• Webhook部署模式，更稳定
• 自动故障恢复机制

⚠️ **2025年10月更新:** 终极版本，包含所有功能！"""

    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_state.record_query(user_id)
    user_data = phone_state.get_user_stats(user_id)
    
    first_seen = datetime.fromisoformat(user_data['first_seen'])
    days_using = (datetime.now() - first_seen).days + 1
    display_name = phone_state.get_user_display_name(user_id)
    
    stats_text = f"""📊 **您的使用统计**

👤 **用户信息:**
• 用户名: {display_name}
• 首次使用: {first_seen.strftime('%Y-%m-%d %H:%M:%S')}
• 使用天数: {days_using} 天

🔍 **查询统计:**
• 总查询次数: {user_data['query_count']:,} 次
• 今日查询: {user_data['queries_today']} 次
• 发现号码: {user_data['phone_numbers_found']:,} 个
• 平均每日: {user_data['query_count']/days_using:.1f} 次"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_state.record_query(user_id)
    
    system_status = phone_state.get_system_status()
    global_stats = phone_state.get_global_stats()
    
    status_text = f"""🔧 **系统状态报告**

⚙️ **系统信息:**
• 运行时间: {system_status['uptime']}
• 处理消息: {system_status['message_count']:,} 条
• 平台: Webhook模式 (云端)

📊 **全局统计:**
• 总用户: {global_stats['total_users']:,} 人
• 总查询: {global_stats['total_queries']:,} 次
• 注册号码: {global_stats['total_registered_phones']:,} 个
• 重复检测: {global_stats['total_duplicates']:,} 次

💡 **版本信息:**
• 机器人版本: **v17.0-终极版** 🚀
• 更新时间: 2025年10月
• 特色功能: 全功能集成+自动重启
• 部署模式: Webhook + 安全防护"""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_state.record_query(user_id)
    
    try:
        success = phone_state.clear_all_data()
        if success:
            clear_text = """🗑️ **数据清理完成**

✅ **已清理的内容:**
• 所有电话号码记录
• 所有用户统计数据  
• 所有重复检测历史
• 系统统计信息

🔄 **系统状态:** 已重置，可重新开始使用

💡 可以继续发送电话号码进行检测!"""
        else:
            clear_text = "❌ 数据清理失败，请重试。"
        
        await update.message.reply_text(clear_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"清理数据错误: {e}")
        await update.message.reply_text("❌ 清理数据时发生错误，请重试。", parse_mode='Markdown')

async def handle_phone_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息"""
    try:
        user_id = update.effective_user.id
        user_info = update.effective_user.to_dict()
        message_text = update.message.text
        
        # 频率限制检查
        if is_rate_limited(user_id):
            await update.message.reply_text(
                "⚠️ 请求过于频繁，请稍后再试。（每分钟最多15次查询）",
                parse_mode='Markdown'
            )
            return
        
        if user_info:
            phone_state.update_user_info(user_id, user_info)
        
        phone_numbers = clean_malaysia_phone_number(message_text)
        
        if not phone_numbers:
            response_text = """❌ **没有检测到有效的马来西亚电话号码**

💡 **支持的格式示例:**
• +60 11-6852 8782
• 011-6852 8782  
• 03-1234 5678
• 60116852782

请发送马来西亚电话号码!"""
            await update.message.reply_text(response_text, parse_mode='Markdown')
            return
        
        analyses = []
        carriers_found = set()
        
        for phone in phone_numbers:
            analysis = analyze_malaysia_phone(phone)
            duplicate_info = phone_state.register_phone_number(phone, user_id, user_info)
            analysis['duplicate_info'] = duplicate_info
            analyses.append(analysis)
            
            if analysis['carrier'] != '未知':
                carriers_found.add(analysis['carrier'])

        phone_state.record_query(user_id, len(phone_numbers), list(carriers_found))

        # 构建响应
        if len(analyses) == 1:
            analysis = analyses[0]
            duplicate_info = analysis['duplicate_info']
            current_time = datetime.now()
            
            response_text = f"""🗣️ 当前号码: {duplicate_info['formatted_phone']}
📍 号码地区: 🇲🇾 {analysis['location']}

👤 当前用户: {duplicate_info['current_user_name']}
⏰ 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}

📊 原始记录:
👤 首次用户: {duplicate_info['first_user_name']}
⏰ 首次时间: {duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}

🎯 统计信息:
📈 历史交叉数: {duplicate_info['occurrence_count']}次
👥 涉及用户: {duplicate_info['total_users']}人"""

            if duplicate_info['is_duplicate']:
                response_text += f"\n\n⚠️ 请注意: 此号码已被使用!"
            else:
                response_text += f"\n\n✅ 新号码: 首次记录!"

        else:
            response_text = f"""🔍 批量检测: 共{len(analyses)}个号码

"""
            
            for i, analysis in enumerate(analyses, 1):
                duplicate_info = analysis['duplicate_info']
                current_time = datetime.now()
                
                response_text += f"""───── 号码 {i} ─────
🗣️ 当前号码: {duplicate_info['formatted_phone']}
📍 号码地区: 🇲🇾 {analysis['location']}

👤 当前用户: {duplicate_info['current_user_name']}
⏰ 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}

📊 原始记录:
👤 首次用户: {duplicate_info['first_user_name']}
⏰ 首次时间: {duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}

🎯 统计信息:
📈 历史交叉数: {duplicate_info['occurrence_count']}次
👥 涉及用户: {duplicate_info['total_users']}人"""

                if duplicate_info['is_duplicate']:
                    response_text += f"\n⚠️ 请注意: 此号码已被使用!\n\n"
                else:
                    response_text += f"\n✅ 新号码: 首次记录!\n\n"
        
        # 分块发送长消息
        max_length = 4000
        if len(response_text) > max_length:
            parts = [response_text[i:i+max_length] for i in range(0, len(response_text), max_length)]
            for part in parts:
                await update.message.reply_text(part, parse_mode='Markdown')
                await asyncio.sleep(0.5)
        else:
            await update.message.reply_text(response_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理电话号码消息错误: {e}")
        await update.message.reply_text("❌ 处理错误，请重试。", parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理消息"""
    try:
        text = update.message.text.strip()
        
        # 检查是否包含数字（可能是电话号码）
        if any(char.isdigit() for char in text):
            await handle_phone_message(update, context)
        else:
            response = "❌ 请发送一个有效的马来西亚电话号码\n\n使用 /help 查看使用说明"
            await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text(
            "❌ 系统暂时繁忙，请稍后重试。",
            parse_mode='Markdown'
        )

def init_telegram_app():
    """初始化Telegram应用"""
    global application
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("clear", clear_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("✅ Telegram应用初始化成功")
        return True
    except Exception as e:
        logger.error(f"❌ Telegram应用初始化失败: {e}")
        return False

def process_telegram_update(update_data):
    """在线程池中处理Telegram更新"""
    try:
        if not application:
            logger.error("Telegram应用未初始化")
            return
        
        # 创建Update对象
        update = Update.de_json(update_data, application.bot)
        
        if update:
            # 在新的事件循环中处理更新
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(application.process_update(update))
            finally:
                loop.close()
        
    except Exception as e:
        logger.error(f"处理Telegram更新时出错: {e}")

def auto_set_webhook():
    """自动设置webhook"""
    try:
        webhook_endpoint = f"{WEBHOOK_URL}/webhook"
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        
        data = {
            'url': webhook_endpoint,
            'max_connections': 40,
            'allowed_updates': ['message']
        }
        
        response = requests.post(url, data=data, timeout=30)
        result = response.json()
        
        if result.get('ok'):
            logger.info(f"✅ Webhook设置成功: {webhook_endpoint}")
            return True
        else:
            logger.error(f"❌ Webhook设置失败: {result}")
            return False
            
    except Exception as e:
        logger.error(f"设置webhook时出错: {e}")
        return False

# Flask路由
@app.route('/webhook', methods=['POST'])
def webhook():
    """处理Telegram webhook"""
    try:
        # 获取JSON数据
        update_data = request.get_json()
        
        if update_data:
            # 在线程池中异步处理更新
            executor.submit(process_telegram_update, update_data)
        
        return 'OK', 200
        
    except Exception as e:
        logger.error(f"Webhook处理错误: {e}")
        return 'Error', 500

@app.route('/')
def index():
    system_status = phone_state.get_system_status()
    global_stats = phone_state.get_global_stats()
    
    return f'''
    <h1>🇲🇾 马来西亚手机号归属地机器人</h1>
    <p><strong>终极版 v17.0 运行中 🚀</strong></p>
    <p>当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>运行时间: {system_status['uptime']}</p>
    <p>Webhook URL: {WEBHOOK_URL}/webhook</p>
    
    <h2>📊 统计信息</h2>
    <ul>
        <li>总用户: {global_stats['total_users']:,} 人</li>
        <li>总查询: {global_stats['total_queries']:,} 次</li>
        <li>注册号码: {global_stats['total_registered_phones']:,} 个</li>
        <li>重复检测: {global_stats['total_duplicates']:,} 次</li>
        <li>处理消息: {system_status['message_count']:,} 条</li>
    </ul>
    
    <h2>✅ 功能状态</h2>
    <ul>
        <li>✅ 运营商数据已修正</li>
        <li>✅ 号码注册表</li>
        <li>✅ 重复检测</li>
        <li>✅ 用户统计</li>
        <li>✅ 系统监控</li>
        <li>✅ 线程池管理</li>
        <li>✅ 频率限制保护</li>
        <li>✅ 自动webhook设置</li>
        <li>✅ 异常重启机制</li>
    </ul>
    '''

@app.route('/health')
def health():
    """健康检查端点"""
    try:
        system_status = phone_state.get_system_status()
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'version': 'v17.0-终极版',
            'uptime': system_status['uptime'],
            'message_count': system_status['message_count'],
            'telegram_app': application is not None,
            'heartbeat_count': system_status['heartbeat_count']
        })
    except Exception as e:
        logger.error(f"健康检查错误: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stats')
def stats():
    """统计信息端点"""
    try:
        global_stats = phone_state.get_global_stats()
        system_status = phone_state.get_system_status()
        
        return jsonify({
            'global_stats': global_stats,
            'system_status': system_status,
            'active_requests': len(request_times),
            'version': 'v17.0-终极版'
        })
    except Exception as e:
        logger.error(f"获取统计信息错误: {e}")
        return jsonify({'error': str(e)}), 500

# 自动重启机制
class AutoRestarter:
    """自动重启管理器"""
    def __init__(self):
        self.restart_count = 0
        self.max_restarts = 5
        self.last_restart = None
        
    def should_restart(self, error):
        """判断是否应该重启"""
        now = datetime.now()
        
        # 如果是第一次错误，或距离上次重启超过1小时
        if self.last_restart is None or (now - self.last_restart).seconds > 3600:
            self.restart_count = 0
        
        if self.restart_count < self.max_restarts:
            self.restart_count += 1
            self.last_restart = now
            logger.warning(f"准备重启 ({self.restart_count}/{self.max_restarts}): {error}")
            return True
        
        logger.error(f"达到最大重启次数，停止重启: {error}")
        return False
    
    def restart_app(self):
        """重启应用"""
        try:
            logger.info("🔄 执行自动重启...")
            os.execv(sys.executable, ['python'] + sys.argv)
        except Exception as e:
            logger.error(f"重启失败: {e}")

# 全局重启器
auto_restarter = AutoRestarter()

def signal_handler(signum, frame):
    """信号处理器"""
    logger.info(f"🛑 接收到信号 {signum}，准备关闭...")
    try:
        executor.shutdown(wait=True)
        logger.info("✅ 清理完成")
    finally:
        sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    logger.info("🚀 启动终极版Webhook机器人...")
    logger.info(f"端口: {PORT}")
    logger.info(f"Webhook URL: {WEBHOOK_URL}")
    
    try:
        # 初始化Telegram应用
        if not init_telegram_app():
            logger.error("❌ Telegram应用初始化失败，退出")
            sys.exit(1)
        
        # 自动设置webhook（如果配置了正确的URL）
        if WEBHOOK_URL != 'https://your-app-name.onrender.com':
            logger.info("🔧 自动设置webhook...")
            if auto_set_webhook():
                logger.info("✅ Webhook设置成功")
            else:
                logger.warning("⚠️ Webhook设置失败，请检查配置")
        else:
            logger.warning("⚠️ 请配置正确的WEBHOOK_URL环境变量")
        
        logger.info("✅ 所有系统就绪，启动Flask服务器...")
        
        # 启动Flask应用
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"❌ 程序运行错误: {e}")
        if auto_restarter.should_restart(e):
            auto_restarter.restart_app()
        else:
            logger.error("🛑 程序异常退出")
            sys.exit(1)
    finally:
        logger.info("🔄 程序结束")
        executor.shutdown(wait=True)
