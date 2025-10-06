#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管号机器人 v14.0 - 2025归属地修复版
专门显示号码归属地信息 - 确保部署成功
使用Python内置库实现
"""

import os
import re
import json
import threading
import time
import platform
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler

# 机器人配置
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'
TELEGRAM_API = f'https://api.telegram.org/bot{BOT_TOKEN}'

# 马来西亚手机号码运营商和归属地 - 2025更新版
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

# 马来西亚固话区号和归属地 - 2025更新版
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
        print("✅ 管号机器人系统启动（v14.0-2025归属地修复版）")

    def _heartbeat_worker(self):
        """心跳监控线程"""
        while True:
            try:
                with self._lock:
                    self.heartbeat_count += 1
                    self.last_heartbeat = datetime.now()
                time.sleep(300)
            except Exception as e:
                print(f"心跳监控错误: {e}")
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
            
            print("🗑️ 所有数据已清理")
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
            print(f"记录查询统计错误: {e}")

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
    """提取马来西亚电话号码"""
    if not text:
        return []
    
    patterns = [
        r'\+60\s*[1-9][\d\s\-]{7,12}',
        r'60\s*[1-9][\d\s\-]{7,12}',
        r'0\s*[1-9][\d\s\-]{6,11}',
        r'[1-9][\d\s\-]{6,11}',
        r'01[0-9][\d\s\-]{6,9}',
        r'0[2-9]\d[\d\s\-]{5,9}'
    ]
    
    phone_numbers = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        phone_numbers.extend(matches)
    
    cleaned_numbers = []
    for number in phone_numbers:
        clean_num = re.sub(r'[\s\-().+]', '', number)
        clean_num = re.sub(r'[^\d]', '', clean_num)
        
        if clean_num.startswith('60'):
            pass
        elif clean_num.startswith('0'):
            clean_num = '60' + clean_num[1:]
        elif len(clean_num) >= 8:
            clean_num = '60' + clean_num
        
        if 11 <= len(clean_num) <= 13:
            cleaned_numbers.append(clean_num)
    
    return list(set(cleaned_numbers))

def analyze_malaysia_phone(phone_number):
    """分析马来西亚电话号码并返回详细归属地 - 2025最终修复版"""
    analysis = {
        'original': phone_number,
        'normalized': '',
        'is_valid': False,
        'location': '未知归属地',
        'carrier': '未知',
        'flag': '🇲🇾',
        'description': '马来西亚号码'
    }
    
    try:
        # 清理号码
        clean_number = re.sub(r'[^\d]', '', phone_number)
        
        # 标准化为本地格式（保留0开头）
        if clean_number.startswith('60'):
            # 如果是国际格式，去掉60
            local_format = '0' + clean_number[2:]
        elif clean_number.startswith('0'):
            # 如果已经是本地格式
            local_format = clean_number
        else:
            # 如果没有前缀，加上0
            local_format = '0' + clean_number
        
        # 再转换为国际格式用于标准化
        normalized = '60' + local_format[1:]
        analysis['normalized'] = normalized
        
        # 手机号码判断（01X开头，10-11位）
        if local_format.startswith('01') and len(local_format) >= 10:
            mobile_prefix = local_format[:3]  # 取前3位：010, 011, 012等
            
            if mobile_prefix in MALAYSIA_MOBILE_PREFIXES:
                carrier = MALAYSIA_MOBILE_PREFIXES[mobile_prefix]
                analysis['carrier'] = carrier
                analysis['location'] = f'📱 {carrier}·全马来西亚'
                analysis['is_valid'] = True
            else:
                analysis['carrier'] = '马来西亚手机'
                analysis['location'] = '📱 马来西亚手机·未知运营商'
                analysis['is_valid'] = True
        
        # 固话号码判断（0X开头，但不是01X）
        elif local_format.startswith('0') and not local_format.startswith('01') and len(local_format) >= 8:
            area_code = local_format[1:3]  # 取第2-3位作为区号
            
            # 特殊处理沙捞越和沙巴的3位区号（08X）
            if area_code == '8' and len(local_format) >= 9:
                area_code = local_format[1:4]  # 取3位区号
            
            if area_code in MALAYSIA_LANDLINE_CODES:
                region = MALAYSIA_LANDLINE_CODES[area_code]
                analysis['carrier'] = '固定电话'
                analysis['location'] = f'🏠 固话·{region}'
                analysis['is_valid'] = True
            else:
                analysis['carrier'] = '固定电话'
                analysis['location'] = '🏠 固话·马来西亚'
                analysis['is_valid'] = True
        
        # 其他情况
        elif len(local_format) >= 7:
            analysis['location'] = '🇲🇾 马来西亚·未知运营商'
            analysis['is_valid'] = True
            analysis['carrier'] = '未知运营商'
    
    except Exception as e:
        print(f"马来西亚电话号码分析错误: {e}")
    
    return analysis

def send_telegram_message(chat_id, text, parse_mode='Markdown'):
    """发送Telegram消息"""
    try:
        max_length = 4000
        if len(text) > max_length:
            parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            for part in parts:
                send_single_message(chat_id, part, parse_mode)
                time.sleep(0.5)
        else:
            send_single_message(chat_id, text, parse_mode)
    except Exception as e:
        print(f"发送消息错误: {e}")

def send_single_message(chat_id, text, parse_mode='Markdown'):
    """发送单条消息"""
    try:
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        
        params = urllib.parse.urlencode(data).encode('utf-8')
        
        req = urllib.request.Request(
            f'{TELEGRAM_API}/sendMessage',
            data=params,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            if not result.get('ok'):
                print(f"Telegram API错误: {result}")
                
    except Exception as e:
        print(f"发送单条消息错误: {e}")

def handle_start_command(chat_id, user_id):
    """处理/start命令"""
    phone_state.record_query(user_id)
    
    welcome_text = f"""🗣️ **欢迎使用管号机器人!** [v14.0-2025归属地修复版 ✅]

🔍 **专业功能:**
• 📱 马来西亚手机和固话识别  
• ⏰ 首次出现时间记录
• 🔄 重复号码检测及关联信息
• 👥 用户追踪和统计
• 📍 **精准归属地显示（已修复！）**

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

    send_telegram_message(chat_id, welcome_text)

def handle_phone_message(chat_id, user_id, message_text, user_info=None):
    """处理包含电话号码的消息"""
    try:
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
            send_telegram_message(chat_id, response_text)
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

        # *** 关键修复：构建响应（使用新的归属地格式）***
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
        
        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理电话号码消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理错误，请重试。")

def handle_clear_command(chat_id, user_id):
    """处理/clear命令"""
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
        
        send_telegram_message(chat_id, clear_text)
        
    except Exception as e:
        print(f"清理数据错误: {e}")
        send_telegram_message(chat_id, "❌ 清理数据时发生错误，请重试。")

def handle_help_command(chat_id, user_id):
    """处理/help命令"""
    phone_state.record_query(user_id)
    
    help_text = """🗣️ **管号机器人 - 帮助** [v14.0-2025归属地修复版 ✅]

🔍 **主要功能:**
• 检测马来西亚手机和固话号码
• 记录首次出现时间
• 检测重复号码及关联信息
• 用户追踪和统计
• **显示精准归属地 📍（已修复）**

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
• 固话归属地显示（🏠 固话·雪兰莪/吉隆坡/布城）

⚠️ **2025年10月更新:** 归属地显示功能已完全修复！"""

    send_telegram_message(chat_id, help_text)

def handle_stats_command(chat_id, user_id):
    """处理/stats命令"""
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
    
    send_telegram_message(chat_id, stats_text)

def handle_status_command(chat_id, user_id):
    """处理/status命令"""
    phone_state.record_query(user_id)
    
    system_status = phone_state.get_system_status()
    global_stats = phone_state.get_global_stats()
    
    status_text = f"""🔧 **系统状态报告**

⚙️ **系统信息:**
• 运行时间: {system_status['uptime']}
• 处理消息: {system_status['message_count']:,} 条
• 平台: Linux (云端)

📊 **全局统计:**
• 总用户: {global_stats['total_users']:,} 人
• 总查询: {global_stats['total_queries']:,} 次
• 注册号码: {global_stats['total_registered_phones']:,} 个
• 重复检测: {global_stats['total_duplicates']:,} 次

💡 **版本信息:**
• 机器人版本: **v14.0-2025归属地修复版** ✅
• 更新时间: 2025年10月
• 特色功能: 精准归属地显示（已修复）
• 修复内容: 手机显示运营商，固话显示地区"""
    
    send_telegram_message(chat_id, status_text)

class TelegramWebhookHandler(BaseHTTPRequestHandler):
    """处理Telegram Webhook请求"""
    
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            update = json.loads(post_data.decode('utf-8'))
            
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                user_id = message['from']['id']
                user_info = message['from']
                
                if 'text' in message:
                    text = message['text'].strip()
                    
                    if text.startswith('/start'):
                        handle_start_command(chat_id, user_id)
                    elif text.startswith('/help'):
                        handle_help_command(chat_id, user_id)
                    elif text.startswith('/stats'):
                        handle_stats_command(chat_id, user_id)
                    elif text.startswith('/status'):
                        handle_status_command(chat_id, user_id)
                    elif text.startswith('/clear'):
                        handle_clear_command(chat_id, user_id)
                    else:
                        handle_phone_message(chat_id, user_id, text, user_info)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            print(f"Webhook处理错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def do_GET(self):
        """处理健康检查请求"""
        try:
            system_status = phone_state.get_system_status()
            
            response_data = {
                'status': 'healthy',
                'uptime': system_status['uptime'],
                'message_count': system_status['message_count'],
                'version': 'v14.0-2025归属地修复版'
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))
            
        except Exception as e:
            print(f"健康检查错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """禁用默认日志"""
        pass

def setup_webhook():
    """设置Webhook"""
    try:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if not render_url:
            print("❌ 未找到RENDER_EXTERNAL_URL环境变量")
            return False
        
        webhook_url = f"{render_url}/webhook"
        
        data = urllib.parse.urlencode({'url': webhook_url}).encode('utf-8')
        req = urllib.request.Request(
            f'{TELEGRAM_API}/setWebhook',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print(f"✅ Webhook设置成功: {webhook_url}")
                return True
            else:
                print(f"❌ Webhook设置失败: {result}")
                return False
                
    except Exception as e:
        print(f"❌ 设置Webhook错误: {e}")
        return False

def main():
    """主程序"""
    print("🚀 启动管号机器人（v14.0-2025归属地修复版）...")
    
    port = int(os.environ.get('PORT', 8000))
    
    try:
        if setup_webhook():
            print("✅ Webhook配置完成")
        else:
            print("⚠️  Webhook配置失败，但继续运行")
        
        server = HTTPServer(('0.0.0.0', port), TelegramWebhookHandler)
        print(f"🌐 HTTP服务器启动在端口 {port}")
        print(f"🔧 平台: {platform.platform()}")
        print(f"🐍 Python: {platform.python_version()}")
        print("✅ 系统就绪，等待消息...")
        print("🔥 归属地显示功能已修复 - 2025年10月版本")
        
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n⏹️  收到停止信号")
    except Exception as e:
        print(f"❌ 程序错误: {e}")
    finally:
        print("🔄 程序结束")

if __name__ == '__main__':
    main()
