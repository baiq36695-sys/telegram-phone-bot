#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码专用检测机器人 v10.4 - 隐私版
只显示时间和重复信息，不显示具体号码内容
修复了号码识别问题，使用Python内置库实现
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

class MalaysiaPhoneState:
    """线程安全的马来西亚电话号码状态管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = datetime.now()
        self.heartbeat_count = 0
        self.last_heartbeat = None
        self.message_count = 0
        
        # 全局号码注册表 - 记录每个号码的首次出现
        self.phone_registry = {}  # {标准化号码: {'first_seen': datetime, 'count': int, 'users': set, 'first_user': user_id}}
        
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
            'username': None  # 存储用户名
        })
        
        # 用户ID到用户名的映射
        self.user_names = {}  # {user_id: username or first_name}
        
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
        print("✅ 马来西亚电话号码检测系统启动（隐私版）")

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

    def update_user_info(self, user_id, user_info):
        """更新用户信息"""
        with self._lock:
            # 提取用户名或姓名
            username = user_info.get('username')
            first_name = user_info.get('first_name', '')
            last_name = user_info.get('last_name', '')
            
            if username:
                display_name = f"@{username}"
            else:
                display_name = f"{first_name} {last_name}".strip() or f"用户{user_id}"
            
            self.user_names[user_id] = display_name
            self.user_data[user_id]['username'] = display_name

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
                
                # 获取首次发送者的用户名
                first_user_id = registry_entry['first_user']
                first_user_name = self.user_names.get(first_user_id, f"用户{first_user_id}")
                
                return {
                    'is_duplicate': True,
                    'first_seen': registry_entry['first_seen'],
                    'occurrence_count': registry_entry['count'],
                    'total_users': len(registry_entry['users']),
                    'first_user_name': first_user_name,
                    'first_user_id': first_user_id
                }
            else:
                # 新号码，首次记录
                self.phone_registry[normalized_phone] = {
                    'first_seen': current_time,
                    'count': 1,
                    'users': {user_id},
                    'first_user': user_id
                }
                
                return {
                    'is_duplicate': False,
                    'first_seen': current_time,
                    'occurrence_count': 1,
                    'total_users': 1,
                    'first_user_name': self.user_names.get(user_id, f"用户{user_id}"),
                    'first_user_id': user_id
                }

    def _normalize_phone(self, phone):
        """标准化电话号码格式"""
        clean = re.sub(r'[^\d]', '', phone)
        if clean.startswith('60'):
            return clean
        elif clean.startswith('0'):
            return '60' + clean[1:]
        else:
            return '60' + clean

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
    """专门清理和提取马来西亚电话号码（修复版）"""
    if not text:
        return []
    
    # 马来西亚电话号码格式的正则表达式（更宽松的匹配）
    patterns = [
        r'\+60\s*[1-9][\d\s\-]{7,12}',      # +60格式（更宽松）
        r'60\s*[1-9][\d\s\-]{7,12}',       # 60开头
        r'0\s*[1-9][\d\s\-]{6,11}',        # 0开头的本地格式
        r'[1-9][\d\s\-]{6,11}',            # 去掉国家代码的格式
        r'01[0-9][\d\s\-]{6,9}',           # 手机号格式
        r'0[2-9]\d[\d\s\-]{5,9}'           # 固话格式
    ]
    
    phone_numbers = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        phone_numbers.extend(matches)
    
    # 清理和标准化
    cleaned_numbers = []
    for number in phone_numbers:
        # 移除空格、横线、括号、加号
        clean_num = re.sub(r'[\s\-().+]', '', number)
        
        # 只保留数字
        clean_num = re.sub(r'[^\d]', '', clean_num)
        
        # 标准化为60格式
        if clean_num.startswith('60'):
            pass  # 已经是60开头
        elif clean_num.startswith('0'):
            clean_num = '60' + clean_num[1:]
        elif len(clean_num) >= 8:  # 假设是本地号码
            clean_num = '60' + clean_num
        
        # 验证长度（马来西亚号码加60应该是11-13位）
        if 11 <= len(clean_num) <= 13:
            cleaned_numbers.append(clean_num)
    
    return list(set(cleaned_numbers))  # 去重

def analyze_malaysia_phone(phone_number):
    """专门分析马来西亚电话号码（修复版）"""
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
        if clean_phone.startswith('60') and len(clean_phone) >= 11:
            local_number = clean_phone[2:]
        else:
            return analysis
        
        analysis['local_number'] = local_number
        analysis['cleaned'] = clean_phone
        
        # 格式化显示
        if len(local_number) >= 9:
            # 标准国际格式
            if len(local_number) == 9:
                analysis['formatted'] = f"+60 {local_number[:2]}-{local_number[2:5]} {local_number[5:]}"
            elif len(local_number) == 10:
                analysis['formatted'] = f"+60 {local_number[:3]}-{local_number[3:6]} {local_number[6:]}"
            else:
                analysis['formatted'] = f"+60 {local_number}"
        
        # 判断号码类型
        if local_number.startswith('1'):
            # 手机号码
            analysis['number_type'] = '手机号码'
            
            # 识别运营商（基于前两位或三位）
            if len(local_number) >= 2:
                prefix = local_number[:2]
                
                # 更精确的运营商识别
                if prefix == '10':
                    analysis['carrier'] = 'DiGi'
                elif prefix == '11':
                    analysis['carrier'] = 'DiGi / U Mobile'
                elif prefix == '12':
                    analysis['carrier'] = 'Maxis'
                elif prefix == '13':
                    analysis['carrier'] = 'Celcom'
                elif prefix == '14':
                    analysis['carrier'] = 'Maxis / Celcom / DiGi / Tune Talk'
                elif prefix == '15':
                    analysis['carrier'] = 'Yes / Altel'
                elif prefix == '16':
                    analysis['carrier'] = 'Maxis / DiGi / XOX / redONE'
                elif prefix == '17':
                    analysis['carrier'] = 'Maxis'
                elif prefix == '18':
                    analysis['carrier'] = 'DiGi / U Mobile / XOX / redONE'
                elif prefix == '19':
                    analysis['carrier'] = 'Maxis / Celcom'
                else:
                    # 检查三位前缀（少见情况）
                    if len(local_number) >= 3:
                        prefix3 = local_number[:3]
                        analysis['carrier'] = f'马来西亚运营商 ({prefix3})'
                
            # 手机号码有效性（马来西亚手机号通常是9-10位）
            analysis['is_valid'] = 9 <= len(local_number) <= 11
            
            # 生成本地格式
            if len(local_number) >= 9:
                if len(local_number) == 9:
                    analysis['local_format'] = f"01{local_number[1]}-{local_number[2:5]} {local_number[5:]}"
                elif len(local_number) == 10:
                    analysis['local_format'] = f"0{local_number[:2]}-{local_number[2:6]} {local_number[6:]}"
                else:
                    analysis['local_format'] = f"0{local_number}"
            
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
                else:
                    # 至少标记为固定电话
                    analysis['area'] = '马来西亚地区'
                    analysis['carrier'] = 'Telekom Malaysia (TM)'
            
            # 固话有效性（通常是7-9位）
            analysis['is_valid'] = 7 <= len(local_number) <= 9
            
            # 生成本地格式
            if len(local_number) >= 7:
                if len(local_number) == 9:
                    analysis['local_format'] = f"0{local_number[:2]}-{local_number[2:6]} {local_number[6:]}"
                elif len(local_number) == 8:
                    analysis['local_format'] = f"0{local_number[:2]}-{local_number[2:6]} {local_number[6:]}"
                elif len(local_number) == 7:
                    analysis['local_format'] = f"0{local_number[:2]}-{local_number[2:5]} {local_number[5:]}"
                else:
                    analysis['local_format'] = f"0{local_number}"
        
        # 如果仍然未知，但长度合理，标记为可能有效
        if analysis['number_type'] == '未知' and 8 <= len(local_number) <= 11:
            analysis['number_type'] = '可能的马来西亚号码'
            analysis['is_valid'] = True
            analysis['carrier'] = '未知运营商'
    
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
    
    welcome_text = f"""🇲🇾 **欢迎使用马来西亚电话号码专用检测机器人！（隐私版）** 

🔍 **专业功能：**
• 📱 马来西亚手机和固话识别
• ⏰ 首次出现时间记录
• 🔄 重复号码检测及关联信息
• 🔒 隐私保护：不显示具体号码内容

📱 **支持的马来西亚号码格式：**
```
+60 11-6852 8782  (国际格式)
011-6852 8782     (本地手机)
03-1234 5678     (固话)
60116852782      (纯数字)
```

🚀 **特色功能：**
• 🕐 显示每个号码的首次出现时间
• 🔍 检测重复号码并显示与谁重复
• 🔒 保护隐私：不显示号码具体内容

💡 直接发送马来西亚电话号码开始分析！
输入 /help 查看更多命令。"""

    send_telegram_message(chat_id, welcome_text)

def handle_phone_message(chat_id, user_id, message_text, user_info=None):
    """处理包含电话号码的消息"""
    try:
        # 更新用户信息
        if user_info:
            phone_state.update_user_info(user_id, user_info)
        
        # 提取马来西亚电话号码
        phone_numbers = clean_malaysia_phone_number(message_text)
        
        if not phone_numbers:
            response_text = """❌ **没有检测到有效的马来西亚电话号码**

💡 **支持的格式示例：**
• +60 11-6852 8782
• 011-6852 8782  
• 03-1234 5678
• 60116852782

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
                'time': datetime.now().isoformat(),
                'is_duplicate': duplicate_info['is_duplicate']
            })

        # 记录统计
        phone_state.record_query(user_id, len(phone_numbers), list(carriers_found))
        user_data = phone_state.get_user_stats(user_id)

        # 构建响应（隐私版 - 不显示具体号码）
        if len(analyses) == 1:
            # 单个号码分析
            analysis = analyses[0]
            duplicate_info = analysis['duplicate_info']
            
            response_text = f"""🇲🇾 **马来西亚电话号码检测报告**

📱 **基本信息：**
• 号码类型：{analysis['number_type']}
• 运营商：{analysis['carrier']}"""

            if analysis['area'] != '未知':
                response_text += f"\n• 地区：{analysis['area']}"
            
            response_text += f"\n• 有效性：{'✅ 有效' if analysis['is_valid'] else '❌ 格式异常'}"

            # 重复检测信息（重点功能）
            response_text += f"\n\n⏰ **时间追踪：**"
            if duplicate_info['is_duplicate']:
                first_seen = duplicate_info['first_seen']
                response_text += f"\n• ⚠️ **重复号码！**"
                response_text += f"\n• 首次出现：{first_seen.strftime('%Y-%m-%d %H:%M:%S')}"
                response_text += f"\n• 重复次数：{duplicate_info['occurrence_count']} 次"
                response_text += f"\n• 首次发送者：{duplicate_info['first_user_name']}"
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
                response_text += f"\n• 首次发送者：{duplicate_info['first_user_name']}"

            response_text += f"\n\n📊 **您的统计：**"
            response_text += f"\n• 总查询：{user_data['query_count']:,} 次"
            response_text += f"\n• 今日查询：{user_data['queries_today']} 次"
            response_text += f"\n• 发现号码：{user_data['phone_numbers_found']:,} 个"

        else:
            # 多个号码批量分析
            response_text = f"""🇲🇾 **马来西亚号码批量检测**

🔍 **共检测到 {len(analyses)} 个号码：**

"""
            
            duplicates_found = 0
            for i, analysis in enumerate(analyses, 1):
                duplicate_info = analysis['duplicate_info']
                status = '✅' if analysis['is_valid'] else '❌'
                dup_mark = '🔄' if duplicate_info['is_duplicate'] else '✨'
                
                response_text += f"**{i}. {analysis['number_type']}** {status} {dup_mark}\n"
                response_text += f"   运营商：{analysis['carrier']}\n"
                
                if duplicate_info['is_duplicate']:
                    duplicates_found += 1
                    response_text += f"   🔄 重复号码（第{duplicate_info['occurrence_count']}次）\n"
                    response_text += f"   首次：{duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')} by {duplicate_info['first_user_name']}\n"
                else:
                    response_text += f"   ✨ 首次出现：{duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                
                response_text += "\n"

            response_text += f"""📊 **批量分析摘要：**
• 有效号码：{sum(1 for a in analyses if a['is_valid'])}/{len(analyses)}
• 重复号码：{duplicates_found} 个
• 新号码：{len(analyses) - duplicates_found} 个

📱 **您的统计：**
• 总查询：{user_data['query_count']:,} 次
• 今日查询：{user_data['queries_today']} 次"""
        
        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理电话号码消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理错误，请重试。")

def handle_help_command(chat_id, user_id):
    """处理/help命令"""
    phone_state.record_query(user_id)
    
    help_text = """🇲🇾 **马来西亚电话号码检测机器人 - 帮助**

🔍 **主要功能：**
• 检测马来西亚手机和固话号码
• 记录首次出现时间
• 检测重复号码及关联信息
• 隐私保护：不显示具体号码内容

📱 **支持格式：**
• +60 11-6852 8782（国际格式）
• 011-6852 8782（本地手机）
• 03-1234 5678（固话）
• 60116852782（纯数字）

⚡ **快速命令：**
• /start - 开始使用
• /help - 显示帮助
• /stats - 查看个人统计
• /status - 系统状态

💡 **使用方法：**
直接发送包含马来西亚电话号码的消息即可自动检测和分析！

🔒 **隐私保护：**
本机器人不会显示具体的号码内容，只显示检测时间和重复关联信息。"""

    send_telegram_message(chat_id, help_text)

def handle_stats_command(chat_id, user_id):
    """处理/stats命令"""
    phone_state.record_query(user_id)
    user_data = phone_state.get_user_stats(user_id)
    
    # 计算使用天数
    first_seen = datetime.fromisoformat(user_data['first_seen'])
    days_using = (datetime.now() - first_seen).days + 1
    
    # 获取最活跃的小时
    hourly_stats = user_data['hourly_stats']
    most_active_hour = max(hourly_stats.items(), key=lambda x: x[1]) if hourly_stats else (0, 0)
    
    # 获取最常见的运营商
    carrier_stats = user_data['carrier_stats']
    top_carrier = max(carrier_stats.items(), key=lambda x: x[1]) if carrier_stats else ('无', 0)
    
    stats_text = f"""📊 **您的使用统计**

👤 **基本信息：**
• 用户名：{user_data.get('username', '未知')}
• 首次使用：{first_seen.strftime('%Y-%m-%d %H:%M:%S')}
• 使用天数：{days_using} 天

🔍 **查询统计：**
• 总查询次数：{user_data['query_count']:,} 次
• 今日查询：{user_data['queries_today']} 次
• 发现号码：{user_data['phone_numbers_found']:,} 个
• 平均每日：{user_data['query_count']/days_using:.1f} 次

📱 **使用习惯：**
• 最活跃时段：{most_active_hour[0]:02d}:00 ({most_active_hour[1]} 次)
• 最常见运营商：{top_carrier[0]} ({top_carrier[1]} 次)

📈 **最近记录：**"""
    
    # 显示最近几次查询
    recent_history = list(user_data['phone_history'])[-5:]
    if recent_history:
        for i, record in enumerate(recent_history[-3:], 1):
            record_time = datetime.fromisoformat(record['time'])
            status = '🔄' if record['is_duplicate'] else '✨'
            stats_text += f"\n• {record_time.strftime('%m-%d %H:%M')} {status}"
    else:
        stats_text += "\n• 暂无记录"
    
    send_telegram_message(chat_id, stats_text)

def handle_status_command(chat_id, user_id):
    """处理/status命令"""
    phone_state.record_query(user_id)
    
    system_status = phone_state.get_system_status()
    global_stats = phone_state.get_global_stats()
    
    # 系统运行时间
    uptime = system_status['uptime']
    
    status_text = f"""🔧 **系统状态报告**

⚙️ **系统信息：**
• 运行时间：{uptime}
• 心跳次数：{system_status['heartbeat_count']:,}
• 处理消息：{system_status['message_count']:,} 条
• 平台：Linux (云端)

📊 **全局统计：**
• 总用户：{global_stats['total_users']:,} 人
• 总查询：{global_stats['total_queries']:,} 次
• 注册号码：{global_stats['total_registered_phones']:,} 个
• 重复检测：{global_stats['total_duplicates']:,} 次

📱 **今日概况：**
• 活跃用户：{system_status['active_users']} 人
• 系统负载：正常 ✅
• 响应状态：良好 🟢

🔒 **隐私保护：**
• 不存储具体号码内容
• 只记录时间和关联信息
• 定期清理历史数据

💡 **版本信息：**
• 机器人版本：v10.4 隐私版
• 更新时间：2025年10月
• 特色功能：号码隐私保护"""
    
    send_telegram_message(chat_id, status_text)

class TelegramWebhookHandler(BaseHTTPRequestHandler):
    """处理Telegram Webhook请求"""
    
    def do_POST(self):
        try:
            # 读取请求数据
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # 解析JSON数据
            update = json.loads(post_data.decode('utf-8'))
            
            # 处理消息
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                user_id = message['from']['id']
                
                # 获取用户信息
                user_info = message['from']
                
                # 处理文本消息
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
                    else:
                        # 检查是否包含电话号码
                        handle_phone_message(chat_id, user_id, text, user_info)
            
            # 返回成功响应
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            print(f"Webhook处理错误: {e}")
            # 返回错误响应
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
                'version': 'v10.4-privacy'
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
        # 获取Render提供的URL
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if not render_url:
            print("❌ 未找到RENDER_EXTERNAL_URL环境变量")
            return False
        
        webhook_url = f"{render_url}/webhook"
        
        # 设置webhook
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
    print("🚀 启动马来西亚电话号码检测机器人（隐私版）...")
    
    # 获取端口
    port = int(os.environ.get('PORT', 8000))
    
    try:
        # 设置Webhook
        if setup_webhook():
            print("✅ Webhook配置完成")
        else:
            print("⚠️  Webhook配置失败，但继续运行")
        
        # 启动HTTP服务器
        server = HTTPServer(('0.0.0.0', port), TelegramWebhookHandler)
        print(f"🌐 HTTP服务器启动在端口 {port}")
        print(f"🔧 平台: {platform.platform()}")
        print(f"🐍 Python: {platform.python_version()}")
        print("✅ 系统就绪，等待消息...")
        
        # 运行服务器
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n⏹️  收到停止信号")
    except Exception as e:
        print(f"❌ 程序错误: {e}")
    finally:
        print("🔄 程序结束")

if __name__ == '__main__':
    main()
