#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码专用检测机器人 v10.3 - 零依赖版本
专注马来西亚号码分析，包含重复检测和时间追踪功能
使用Python内置库实现，避免所有依赖冲突
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

# 马来西亚运营商数据库（详细版）
MALAYSIA_CARRIERS = {
    # Maxis
    '12': 'Maxis', '14': 'Maxis', '16': 'Maxis', '17': 'Maxis', '19': 'Maxis',
    
    # Celcom
    '13': 'Celcom', '14': 'Celcom', '19': 'Celcom',
    
    # DiGi
    '10': 'DiGi', '11': 'DiGi', '14': 'DiGi', '16': 'DiGi', '18': 'DiGi',
    
    # U Mobile
    '11': 'U Mobile', '18': 'U Mobile',
    
    # Tune Talk
    '14': 'Tune Talk',
    
    # XOX
    '16': 'XOX', '18': 'XOX',
    
    # redONE
    '16': 'redONE', '18': 'redONE',
    
    # Yes
    '15': 'Yes',
    
    # Altel
    '15': 'Altel',
}

# 马来西亚州属区号
MALAYSIA_AREA_CODES = {
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

# 国家代码到国旗的映射（保留部分常用的）
COUNTRY_FLAGS = {
    '60': '🇲🇾',    # 马来西亚
    '65': '🇸🇬',    # 新加坡
    '66': '🇹🇭',    # 泰国
    '62': '🇮🇩',    # 印尼
    '84': '🇻🇳',    # 越南
    '63': '🇵🇭',    # 菲律宾
    '86': '🇨🇳',    # 中国
    '852': '🇭🇰',   # 香港
    '91': '🇮🇳',    # 印度
    '1': '🇺🇸',     # 美国
    '44': '🇬🇧',    # 英国
}

class MalaysiaPhoneState:
    """线程安全的马来西亚电话号码状态管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = datetime.now()
        self.heartbeat_count = 0
        self.last_heartbeat = None
        self.message_count = 0
        
        # 全局号码注册表 - 记录每个号码的首次出现
        self.phone_registry = {}  # {标准化号码: {'first_seen': datetime, 'count': int, 'users': set}}
        
        # 用户数据
        self.user_data = defaultdict(lambda: {
            'first_seen': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'query_count': 0,
            'phone_numbers_found': 0,
            'queries_today': 0,
            'last_query_date': None,
            'phone_history': deque(maxlen=100),  # 增加历史记录
            'hourly_stats': defaultdict(int),
            'carrier_stats': defaultdict(int),
            'daily_queries': defaultdict(int)
        })
        
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
        print("✅ 马来西亚电话号码检测系统启动")

    def _heartbeat_worker(self):
        """心跳监控线程"""
        while True:
            try:
                with self._lock:
                    self.heartbeat_count += 1
                    self.last_heartbeat = datetime.now()
                time.sleep(300)  # 5分钟心跳
            except Exception as e:
                print(f"心跳监控错误: {e}")
                time.sleep(60)

    def register_phone_number(self, phone_number, user_id):
        """注册电话号码并检查重复"""
        with self._lock:
            normalized_phone = self._normalize_phone(phone_number)
            current_time = datetime.now()
            
            if normalized_phone in self.phone_registry:
                # 号码已存在，更新统计
                registry_entry = self.phone_registry[normalized_phone]
                registry_entry['count'] += 1
                registry_entry['users'].add(user_id)
                self.global_stats['total_duplicates'] += 1
                
                return {
                    'is_duplicate': True,
                    'first_seen': registry_entry['first_seen'],
                    'occurrence_count': registry_entry['count'],
                    'total_users': len(registry_entry['users'])
                }
            else:
                # 新号码，首次记录
                self.phone_registry[normalized_phone] = {
                    'first_seen': current_time,
                    'count': 1,
                    'users': {user_id}
                }
                
                return {
                    'is_duplicate': False,
                    'first_seen': current_time,
                    'occurrence_count': 1,
                    'total_users': 1
                }

    def _normalize_phone(self, phone):
        """标准化电话号码格式"""
        # 移除所有非数字字符
        clean = re.sub(r'[^\d]', '', phone)
        # 如果以60开头，保留；如果以0开头，添加60；否则添加60
        if clean.startswith('60'):
            return clean
        elif clean.startswith('0'):
            return '60' + clean[1:]
        else:
            return '60' + clean

    def find_duplicate_phones(self, phone_number):
        """查找与指定号码重复的其他号码"""
        normalized = self._normalize_phone(phone_number)
        with self._lock:
            if normalized in self.phone_registry:
                registry_entry = self.phone_registry[normalized]
                if registry_entry['count'] > 1:
                    return {
                        'has_duplicates': True,
                        'first_seen': registry_entry['first_seen'],
                        'total_occurrences': registry_entry['count'],
                        'involved_users': len(registry_entry['users'])
                    }
            return {'has_duplicates': False}

    def record_query(self, user_id, phone_numbers_found=0, carriers=None):
        """记录查询统计"""
        try:
            with self._lock:
                current_time = datetime.now()
                today = current_time.date().isoformat()
                hour = current_time.hour
                
                # 更新用户数据
                user_data = self.user_data[user_id]
                user_data['last_seen'] = current_time.isoformat()
                user_data['query_count'] += 1
                user_data['phone_numbers_found'] += phone_numbers_found
                
                # 每日查询重置
                if user_data['last_query_date'] != today:
                    user_data['queries_today'] = 0
                    user_data['last_query_date'] = today
                
                user_data['queries_today'] += 1
                user_data['hourly_stats'][hour] += 1
                user_data['daily_queries'][today] += 1
                
                # 记录运营商统计
                if carriers:
                    for carrier in carriers:
                        user_data['carrier_stats'][carrier] += 1
                        self.global_stats['carrier_distribution'][carrier] += 1
                
                # 更新全局统计
                self.global_stats['total_queries'] += 1
                self.global_stats['total_phone_numbers'] += phone_numbers_found
                self.global_stats['hourly_distribution'][hour] += 1
                self.global_stats['daily_stats'][today] += 1
                
                # 更新用户总数
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
phone_state = MalaysiaPhoneState()

def clean_malaysia_phone_number(text):
    """专门清理和提取马来西亚电话号码"""
    if not text:
        return []
    
    # 马来西亚电话号码格式的正则表达式
    patterns = [
        r'\+60\s*[1-9]\d{1,2}[\s\-]?\d{3}[\s\-]?\d{4}',  # +60格式
        r'60\s*[1-9]\d{1,2}[\s\-]?\d{3}[\s\-]?\d{4}',   # 60开头
        r'0\s*[1-9]\d{1,2}[\s\-]?\d{3}[\s\-]?\d{4}',    # 0开头的本地格式
        r'[1-9]\d{1,2}[\s\-]?\d{3}[\s\-]?\d{4}',        # 去掉国家代码的格式
        r'01[0-9][\s\-]?\d{3}[\s\-]?\d{4}',             # 手机号格式
        r'0[2-9]\d[\s\-]?\d{3}[\s\-]?\d{4}'             # 固话格式
    ]
    
    phone_numbers = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        phone_numbers.extend(matches)
    
    # 清理和标准化
    cleaned_numbers = []
    for number in phone_numbers:
        # 移除空格、横线、括号
        clean_num = re.sub(r'[\s\-().]', '', number)
        
        # 标准化为+60格式
        if clean_num.startswith('+60'):
            clean_num = clean_num[1:]
        elif clean_num.startswith('60'):
            pass  # 已经是60开头
        elif clean_num.startswith('0'):
            clean_num = '60' + clean_num[1:]
        else:
            clean_num = '60' + clean_num
        
        # 验证长度（马来西亚号码通常是10-11位，加上60应该是12-13位）
        if 10 <= len(clean_num) <= 13:
            cleaned_numbers.append(clean_num)
    
    return list(set(cleaned_numbers))  # 去重

def analyze_malaysia_phone(phone_number):
    """专门分析马来西亚电话号码"""
    analysis = {
        'original': phone_number,
        'cleaned': phone_number,
        'country_code': '+60',
        'country_name': '马来西亚',
        'country_flag': '🇲🇾',
        'number_type': '未知',
        'carrier': '未知',
        'area': '未知',
        'timezone': 'UTC+8',
        'is_valid': False,
        'formatted': phone_number,
        'local_format': phone_number
    }
    
    try:
        # 确保是60开头的标准格式
        clean_phone = phone_number
        if clean_phone.startswith('60'):
            local_number = clean_phone[2:]
        else:
            return analysis
        
        analysis['local_number'] = local_number
        analysis['formatted'] = f"+60 {local_number}"
        
        # 判断号码类型
        if local_number.startswith('1'):
            # 手机号码
            analysis['number_type'] = '手机号码'
            
            # 识别运营商（基于前两位）
            if len(local_number) >= 2:
                prefix = local_number[:2]
                
                # 详细的运营商识别
                if prefix in ['12']:
                    analysis['carrier'] = 'Maxis'
                elif prefix in ['13']:
                    analysis['carrier'] = 'Celcom'
                elif prefix in ['10', '11']:
                    if prefix == '10':
                        analysis['carrier'] = 'DiGi'
                    elif prefix == '11':
                        analysis['carrier'] = 'DiGi / U Mobile'
                elif prefix in ['14']:
                    analysis['carrier'] = 'Maxis / Celcom / DiGi / Tune Talk'
                elif prefix in ['15']:
                    analysis['carrier'] = 'Yes / Altel'
                elif prefix in ['16']:
                    analysis['carrier'] = 'Maxis / DiGi / XOX / redONE'
                elif prefix in ['17']:
                    analysis['carrier'] = 'Maxis'
                elif prefix in ['18']:
                    analysis['carrier'] = 'DiGi / U Mobile / XOX / redONE'
                elif prefix in ['19']:
                    analysis['carrier'] = 'Maxis / Celcom'
                
            # 手机号码有效性（通常是9-10位）
            analysis['is_valid'] = 9 <= len(local_number) <= 10
            
        elif local_number[0] in '23456789':
            # 固定电话
            analysis['number_type'] = '固定电话'
            
            # 识别地区
            if len(local_number) >= 2:
                area_code = local_number[:2]
                if area_code in MALAYSIA_AREA_CODES:
                    analysis['area'] = MALAYSIA_AREA_CODES[area_code]
                    analysis['carrier'] = 'Telekom Malaysia (TM)'
                elif len(local_number) >= 3:
                    area_code = local_number[:3]
                    if area_code in MALAYSIA_AREA_CODES:
                        analysis['area'] = MALAYSIA_AREA_CODES[area_code]
                        analysis['carrier'] = 'Telekom Malaysia (TM)'
            
            # 固话有效性（通常是7-8位）
            analysis['is_valid'] = 7 <= len(local_number) <= 8
        
        # 生成本地格式
        if analysis['number_type'] == '手机号码' and len(local_number) >= 9:
            # 手机格式：012-345 6789
            analysis['local_format'] = f"{local_number[:3]}-{local_number[3:6]} {local_number[6:]}"
        elif analysis['number_type'] == '固定电话' and len(local_number) >= 7:
            # 固话格式：03-1234 5678
            if len(local_number) == 8:
                analysis['local_format'] = f"{local_number[:2]}-{local_number[2:6]} {local_number[6:]}"
            elif len(local_number) == 7:
                analysis['local_format'] = f"{local_number[:2]}-{local_number[2:5]} {local_number[5:]}"
    
    except Exception as e:
        print(f"马来西亚电话号码分析错误: {e}")
    
    return analysis

def send_telegram_message(chat_id, text, parse_mode='Markdown'):
    """发送Telegram消息"""
    try:
        # 分割长消息
        max_length = 4000
        if len(text) > max_length:
            parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            for part in parts:
                send_single_message(chat_id, part, parse_mode)
                time.sleep(0.5)  # 避免速率限制
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
        
        # URL编码
        params = urllib.parse.urlencode(data).encode('utf-8')
        
        # 发送请求
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
    
    welcome_text = f"""🇲🇾 **欢迎使用马来西亚电话号码专用检测机器人！** 

🔍 **专业功能：**
• 📱 马来西亚手机和固话识别
• 🏢 详细运营商信息（Maxis、Celcom、DiGi等）
• 🗺️ 地区识别（州属区号）
• ⏰ 首次出现时间记录
• 🔄 重复号码检测

📱 **支持的马来西亚号码格式：**
```
+60 12-345 6789  (国际格式)
012-345 6789     (本地手机)
03-1234 5678     (固话)
60123456789      (纯数字)
```

🚀 **特色功能：**
• 🕐 显示每个号码的首次出现时间
• 🔍 检测重复号码并显示关联信息
• 📊 马来西亚运营商详细分析
• 🗺️ 州属和地区识别

💡 直接发送马来西亚电话号码开始分析！
输入 /help 查看更多命令。"""

    send_telegram_message(chat_id, welcome_text)

def handle_help_command(chat_id, user_id):
    """处理/help命令"""
    help_text = """📚 **马来西亚电话号码检测帮助**

🔧 **可用命令：**
• `/start` - 开始使用机器人
• `/help` - 显示此帮助信息  
• `/stats` - 查看个人统计信息
• `/global` - 查看全局统计
• `/status` - 查看系统状态

🇲🇾 **马来西亚号码格式支持：**

📱 **手机号码：**
• +60 12-345 6789 (Maxis)
• +60 13-345 6789 (Celcom)  
• +60 10-345 6789 (DiGi)
• +60 11-345 6789 (DiGi/U Mobile)
• +60 15-345 6789 (Yes/Altel)

📞 **固定电话：**
• +60 3-1234 5678 (雪兰莪/吉隆坡)
• +60 4-123 4567 (吉打/槟城)
• +60 7-123 4567 (柔佛)

🏢 **支持的运营商：**
• Maxis、Celcom、DiGi
• U Mobile、Yes、Altel
• Tune Talk、XOX、redONE
• Telekom Malaysia (固话)

⚡ **特殊功能：**
• 🕐 首次出现时间追踪
• 🔄 重复检测和关联显示
• 🗺️ 地区州属识别
• 📊 运营商市场分析

直接发送号码开始分析！ 🚀"""

    send_telegram_message(chat_id, help_text)

def handle_phone_message(chat_id, user_id, message_text):
    """处理包含电话号码的消息"""
    try:
        # 提取马来西亚电话号码
        phone_numbers = clean_malaysia_phone_number(message_text)
        
        if not phone_numbers:
            response_text = """❌ **没有检测到有效的马来西亚电话号码**

💡 **支持的格式示例：**
• +60 12-345 6789
• 012-345 6789  
• 03-1234 5678
• 60123456789

请发送马来西亚电话号码！"""
            send_telegram_message(chat_id, response_text)
            return
        
        # 分析每个号码
        analyses = []
        carriers_found = set()
        
        for phone in phone_numbers:
            analysis = analyze_malaysia_phone(phone)
            
            # 注册号码并检查重复
            duplicate_info = phone_state.register_phone_number(phone, user_id)
            analysis['duplicate_info'] = duplicate_info
            
            analyses.append(analysis)
            
            if analysis['carrier'] != '未知':
                carriers_found.add(analysis['carrier'])
                
            # 记录到历史
            user_data = phone_state.get_user_stats(user_id)
            user_data['phone_history'].append({
                'phone': analysis['formatted'],
                'time': datetime.now().isoformat(),
                'is_duplicate': duplicate_info['is_duplicate']
            })

        # 记录统计
        phone_state.record_query(user_id, len(phone_numbers), list(carriers_found))
        user_data = phone_state.get_user_stats(user_id)

        # 构建响应
        if len(analyses) == 1:
            # 单个号码详细分析
            analysis = analyses[0]
            duplicate_info = analysis['duplicate_info']
            
            response_text = f"""🇲🇾 **马来西亚电话号码分析报告**

📱 **号码信息：**
• 原始号码：`{analysis['original']}`
• 标准格式：`{analysis['formatted']}`
• 本地格式：`{analysis['local_format']}`
• 号码类型：{analysis['number_type']}
• 运营商：{analysis['carrier']}"""

            if analysis['area'] != '未知':
                response_text += f"\n• 地区：{analysis['area']}"
            
            response_text += f"\n• 有效性：{'✅ 有效' if analysis['is_valid'] else '❌ 格式异常'}"

            # 重复检测信息
            response_text += f"\n\n⏰ **时间追踪：**"
            if duplicate_info['is_duplicate']:
                first_seen = duplicate_info['first_seen']
                response_text += f"\n• ⚠️ **重复号码！**"
                response_text += f"\n• 首次出现：{first_seen.strftime('%Y-%m-%d %H:%M:%S')}"
                response_text += f"\n• 重复次数：{duplicate_info['occurrence_count']} 次"
                response_text += f"\n• 涉及用户：{duplicate_info['total_users']} 人"
                
                # 计算时间差
                time_diff = datetime.now() - first_seen
                if time_diff.days > 0:
                    response_text += f"\n• 距首次：{time_diff.days}天前"
                elif time_diff.seconds > 3600:
                    hours = time_diff.seconds // 3600
                    response_text += f"\n• 距首次：{hours}小时前"
                else:
                    minutes = time_diff.seconds // 60
                    response_text += f"\n• 距首次：{minutes}分钟前"
            else:
                response_text += f"\n• ✨ **首次出现！**"
                response_text += f"\n• 记录时间：{duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}"

            response_text += f"\n\n📊 **您的统计：**"
            response_text += f"\n• 总查询：{user_data['query_count']:,} 次"
            response_text += f"\n• 今日查询：{user_data['queries_today']} 次"
            response_text += f"\n• 发现号码：{user_data['phone_numbers_found']:,} 个"

        else:
            # 多个号码批量分析
            response_text = f"""🇲🇾 **马来西亚号码批量分析**

🔍 **共检测到 {len(analyses)} 个号码：**

"""
            
            duplicates_found = 0
            for i, analysis in enumerate(analyses, 1):
                duplicate_info = analysis['duplicate_info']
                status = '✅' if analysis['is_valid'] else '❌'
                dup_mark = '🔄' if duplicate_info['is_duplicate'] else '✨'
                
                response_text += f"""**{i}. {analysis['formatted']}** {status} {dup_mark}
   {analysis['carrier']} | {analysis['number_type']}"""
                
                if duplicate_info['is_duplicate']:
                    duplicates_found += 1
                    response_text += f" | 重复{duplicate_info['occurrence_count']}次"
                else:
                    response_text += f" | 首次出现"
                
                response_text += "\n\n"

            response_text += f"""📊 **批量分析摘要：**
• 有效号码：{sum(1 for a in analyses if a['is_valid'])}/{len(analyses)}
• 重复号码：{duplicates_found} 个
• 涉及运营商：{len(carriers_found)} 家
• 您的总查询：{user_data['query_count']:,} 次

💡 发送单个号码可获取详细重复分析！"""

        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理电话号码消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理消息时出现错误，请稍后重试。")

def handle_stats_command(chat_id, user_id):
    """处理/stats命令"""
    user_data = phone_state.get_user_stats(user_id)
    
    # 基本统计
    first_seen = datetime.fromisoformat(user_data['first_seen'])
    last_seen = datetime.fromisoformat(user_data['last_seen'])
    days_active = (last_seen.date() - first_seen.date()).days + 1
    
    stats_text = f"""📊 **您的马来西亚号码查询统计**

👤 **基本信息：**
• 首次使用：{first_seen.strftime('%Y-%m-%d %H:%M')}
• 最后使用：{last_seen.strftime('%Y-%m-%d %H:%M')}
• 活跃天数：{days_active} 天

🔍 **查询统计：**
• 总查询次数：{user_data['query_count']:,}
• 今日查询：{user_data['queries_today']}
• 发现号码：{user_data['phone_numbers_found']:,} 个
• 平均每日：{user_data['query_count']/days_active:.1f} 次"""

    # 运营商分析
    if user_data['carrier_stats']:
        stats_text += "\n\n📡 **查询运营商分布：**"
        sorted_carriers = sorted(user_data['carrier_stats'].items(), key=lambda x: x[1], reverse=True)[:5]
        for carrier, count in sorted_carriers:
            stats_text += f"\n• {carrier}：{count} 次"

    # 时段分析
    if user_data['hourly_stats']:
        stats_text += "\n\n📈 **活跃时段分析：**"
        sorted_hours = sorted(user_data['hourly_stats'].items(), key=lambda x: x[1], reverse=True)[:3]
        for hour, count in sorted_hours:
            time_period = "早晨" if 6 <= hour < 12 else "下午" if 12 <= hour < 18 else "晚上" if 18 <= hour < 24 else "深夜"
            stats_text += f"\n• {hour:02d}:00 ({time_period})：{count} 次"

    # 最近查询历史
    if user_data['phone_history']:
        stats_text += f"\n\n📱 **最近查询记录：**"
        recent_phones = list(user_data['phone_history'])[-5:]  # 最近5条
        for phone_record in recent_phones:
            if isinstance(phone_record, dict):
                phone_time = datetime.fromisoformat(phone_record['time'])
                dup_mark = '🔄' if phone_record['is_duplicate'] else '✨'
                stats_text += f"\n• {phone_record['phone']} {dup_mark} ({phone_time.strftime('%m-%d %H:%M')})"
            else:
                stats_text += f"\n• {phone_record}"

    stats_text += "\n\n继续查询马来西亚号码获得更多统计！ 🇲🇾"

    send_telegram_message(chat_id, stats_text)

def handle_global_command(chat_id, user_id):
    """处理/global命令"""
    global_stats = phone_state.get_global_stats()
    system_status = phone_state.get_system_status()
    
    # 运行时间计算
    start_time = datetime.fromisoformat(global_stats['start_time'])
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    global_text = f"""🇲🇾 **马来西亚号码全局统计**

⏱️ **系统状态：**
• 运行时间：{days}天 {hours}小时 {minutes}分钟
• 活跃用户：{global_stats['total_users']:,} 人
• 总查询数：{global_stats['total_queries']:,} 次
• 处理号码：{global_stats['total_phone_numbers']:,} 个
• 注册号码：{global_stats['total_registered_phones']:,} 个
• 重复检测：{global_stats['total_duplicates']:,} 次

📊 **使用热度分析：**"""

    # 热门时段
    if global_stats['hourly_distribution']:
        sorted_hours = sorted(global_stats['hourly_distribution'].items(), key=lambda x: x[1], reverse=True)[:5]
        global_text += "\n• 🔥 **热门时段：**"
        for hour, count in sorted_hours:
            time_period = "早晨" if 6 <= hour < 12 else "下午" if 12 <= hour < 18 else "晚上" if 18 <= hour < 24 else "深夜"
            global_text += f"\n  - {hour:02d}:00 ({time_period})：{count} 次"

    # 热门运营商
    if global_stats['carrier_distribution']:
        global_text += "\n\n• 📡 **热门运营商：**"
        sorted_carriers = sorted(global_stats['carrier_distribution'].items(), key=lambda x: x[1], reverse=True)[:8]
        for carrier, count in sorted_carriers:
            percentage = (count / global_stats['total_queries']) * 100
            global_text += f"\n  - {carrier}：{count} 次 ({percentage:.1f}%)"

    # 每日统计趋势
    if global_stats['daily_stats']:
        global_text += "\n\n📈 **最近7天趋势：**"
        recent_days = sorted(global_stats['daily_stats'].items())[-7:]
        for date, count in recent_days:
            date_obj = datetime.fromisoformat(date)
            weekday = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][date_obj.weekday()]
            global_text += f"\n• {date} ({weekday})：{count} 次"

    global_text += f"\n\n💡 号码重复率：{(global_stats['total_duplicates']/max(global_stats['total_phone_numbers'], 1)*100):.1f}%"
    global_text += f"\n🎯 平均每用户查询：{global_stats['total_queries']/max(global_stats['total_users'], 1):.1f} 次"

    send_telegram_message(chat_id, global_text)

def handle_status_command(chat_id, user_id):
    """处理/status命令"""
    system_status = phone_state.get_system_status()
    
    status_text = f"""🔧 **马来西亚号码检测系统状态**

💻 **服务器信息：**
• 系统平台：{platform.system()} {platform.release()}
• Python版本：{platform.python_version()}
• 运行时间：{system_status['uptime']}

📡 **机器人状态：**
• 消息处理：{system_status['message_count']:,} 条
• 活跃用户：{system_status['active_users']:,} 人
• 注册号码：{system_status['registered_phones']:,} 个

❤️ **心跳监控：**
• 心跳次数：{system_status['heartbeat_count']} 次
• 最后心跳：{datetime.fromisoformat(system_status['last_heartbeat']).strftime('%H:%M:%S') if system_status['last_heartbeat'] else '未知'}
• 监控状态：🟢 正常

🌐 **专用功能：**
• 马来西亚号码识别：🟢 正常
• 重复检测系统：🟢 正常
• 时间追踪功能：🟢 正常
• 运营商识别：🟢 正常

✅ 马来西亚专用检测系统运行正常！"""

    send_telegram_message(chat_id, status_text)

def process_telegram_update(update):
    """处理Telegram更新"""
    try:
        if 'message' not in update:
            return
            
        message = update['message']
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        
        # 处理命令
        if 'text' in message:
            text = message['text'].strip()
            
            if text.startswith('/start'):
                handle_start_command(chat_id, user_id)
            elif text.startswith('/help'):
                handle_help_command(chat_id, user_id)
            elif text.startswith('/stats'):
                handle_stats_command(chat_id, user_id)
            elif text.startswith('/global'):
                handle_global_command(chat_id, user_id)
            elif text.startswith('/status'):
                handle_status_command(chat_id, user_id)
            else:
                # 处理普通消息（可能包含马来西亚电话号码）
                handle_phone_message(chat_id, user_id, text)
        
    except Exception as e:
        print(f"处理Telegram更新错误: {e}")

class TelegramWebhookHandler(BaseHTTPRequestHandler):
    """Telegram Webhook处理器"""
    
    def do_POST(self):
        """处理POST请求"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # 解析JSON数据
            update = json.loads(post_data.decode('utf-8'))
            
            # 处理更新
            process_telegram_update(update)
            
            # 响应成功
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode('utf-8'))
            
        except Exception as e:
            print(f"处理POST请求错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def do_GET(self):
        """处理GET请求（健康检查）"""
        try:
            if self.path == '/health':
                # 健康检查
                system_status = phone_state.get_system_status()
                health_data = {
                    'status': 'healthy',
                    'uptime': system_status['uptime'],
                    'message_count': system_status['message_count'],
                    'registered_phones': system_status['registered_phones'],
                    'timestamp': datetime.now().isoformat()
                }
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(health_data).encode('utf-8'))
                
            else:
                # 默认响应
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                
                html_response = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>马来西亚电话号码检测机器人</title>
</head>
<body>
    <h1>🇲🇾 马来西亚电话号码专用检测机器人</h1>
    <p>✅ 服务正在运行</p>
    <p>🚀 零依赖架构，专注马来西亚号码</p>
    <p>⏰ 支持重复检测和时间追踪</p>
    <p>📡 详细运营商识别</p>
    <hr>
    <p>在Telegram中搜索机器人并开始使用！</p>
</body>
</html>
                """
                self.wfile.write(html_response.encode('utf-8'))
                
        except Exception as e:
            print(f"处理GET请求错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """覆盖日志方法以减少输出"""
        pass

def main():
    """主函数"""
    try:
        # 获取端口
        port = int(os.environ.get('PORT', 8000))
        
        print(f"🇲🇾 启动马来西亚电话号码专用检测机器人")
        print(f"📡 服务端口: {port}")
        print(f"⏰ 重复检测: 已启用")
        print(f"🔧 架构: 零依赖")
        
        # 启动HTTP服务器
        server = HTTPServer(('0.0.0.0', port), TelegramWebhookHandler)
        print(f"✅ 马来西亚号码检测服务器启动成功，监听端口 {port}")
        
        # 启动服务
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号，正在关闭服务器...")
    except Exception as e:
        print(f"❌ 服务器启动失败: {e}")
    finally:
        print("👋 服务器已关闭")

if __name__ == '__main__':
    main()
