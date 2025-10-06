#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版电话号码检测机器人 v10.3 - 零依赖版本
移除等级系统，保留核心功能，专为Render Web Service优化
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

# 国家代码到国旗的映射
COUNTRY_FLAGS = {
    '1': '🇺🇸',     # 美国/加拿大
    '44': '🇬🇧',    # 英国
    '33': '🇫🇷',    # 法国
    '49': '🇩🇪',    # 德国
    '39': '🇮🇹',    # 意大利
    '34': '🇪🇸',    # 西班牙
    '7': '🇷🇺',     # 俄罗斯
    '81': '🇯🇵',    # 日本
    '82': '🇰🇷',    # 韩国
    '86': '🇨🇳',    # 中国
    '852': '🇭🇰',   # 香港
    '853': '🇲🇴',   # 澳门
    '886': '🇹🇼',   # 台湾
    '65': '🇸🇬',    # 新加坡
    '60': '🇲🇾',    # 马来西亚
    '66': '🇹🇭',    # 泰国
    '84': '🇻🇳',    # 越南
    '62': '🇮🇩',    # 印尼
    '63': '🇵🇭',    # 菲律宾
    '91': '🇮🇳',    # 印度
    '92': '🇵🇰',    # 巴基斯坦
    '90': '🇹🇷',    # 土耳其
    '98': '🇮🇷',    # 伊朗
    '966': '🇸🇦',   # 沙特
    '971': '🇦🇪',   # 阿联酋
    '972': '🇮🇱',   # 以色列
    '20': '🇪🇬',    # 埃及
    '27': '🇿🇦',    # 南非
    '234': '🇳🇬',   # 尼日利亚
    '55': '🇧🇷',    # 巴西
    '54': '🇦🇷',    # 阿根廷
    '52': '🇲🇽',    # 墨西哥
    '56': '🇨🇱',    # 智利
    '57': '🇨🇴',    # 哥伦比亚
    '51': '🇵🇪',    # 秘鲁
    '61': '🇦🇺',    # 澳大利亚
    '64': '🇳🇿',    # 新西兰
}

# 国家信息数据库
COUNTRIES_DB = {
    '86': {'name': '中国', 'timezone': 'UTC+8', 'mobile_length': [11], 'mobile_prefixes': ['13', '14', '15', '16', '17', '18', '19']},
    '1': {'name': '美国/加拿大', 'timezone': 'UTC-5/-8', 'mobile_length': [10], 'mobile_prefixes': ['2', '3', '4', '5', '6', '7', '8', '9']},
    '44': {'name': '英国', 'timezone': 'UTC+0', 'mobile_length': [10], 'mobile_prefixes': ['7']},
    '81': {'name': '日本', 'timezone': 'UTC+9', 'mobile_length': [10], 'mobile_prefixes': ['70', '80', '90']},
    '82': {'name': '韩国', 'timezone': 'UTC+9', 'mobile_length': [9, 10], 'mobile_prefixes': ['10', '11']},
    '33': {'name': '法国', 'timezone': 'UTC+1', 'mobile_length': [9], 'mobile_prefixes': ['6', '7']},
    '49': {'name': '德国', 'timezone': 'UTC+1', 'mobile_length': [10, 11], 'mobile_prefixes': ['15', '16', '17']},
    '852': {'name': '香港', 'timezone': 'UTC+8', 'mobile_length': [8], 'mobile_prefixes': ['5', '6', '9']},
    '886': {'name': '台湾', 'timezone': 'UTC+8', 'mobile_length': [9], 'mobile_prefixes': ['9']},
    '65': {'name': '新加坡', 'timezone': 'UTC+8', 'mobile_length': [8], 'mobile_prefixes': ['8', '9']},
    '91': {'name': '印度', 'timezone': 'UTC+5:30', 'mobile_length': [10], 'mobile_prefixes': ['6', '7', '8', '9']},
    '7': {'name': '俄罗斯', 'timezone': 'UTC+3/+12', 'mobile_length': [10], 'mobile_prefixes': ['9']},
    '61': {'name': '澳大利亚', 'timezone': 'UTC+10', 'mobile_length': [9], 'mobile_prefixes': ['4']},
    '55': {'name': '巴西', 'timezone': 'UTC-3', 'mobile_length': [10, 11], 'mobile_prefixes': ['1', '2', '3', '4', '5']},
}

# 中国运营商数据库
CHINA_CARRIERS = {
    '130': '中国联通', '131': '中国联通', '132': '中国联通', '155': '中国联通', '156': '中国联通',
    '185': '中国联通', '186': '中国联通', '145': '中国联通', '175': '中国联通', '176': '中国联通',
    '134': '中国移动', '135': '中国移动', '136': '中国移动', '137': '中国移动', '138': '中国移动',
    '139': '中国移动', '150': '中国移动', '151': '中国移动', '152': '中国移动', '157': '中国移动',
    '158': '中国移动', '159': '中国移动', '182': '中国移动', '183': '中国移动', '184': '中国移动',
    '187': '中国移动', '188': '中国移动', '147': '中国移动', '178': '中国移动',
    '133': '中国电信', '153': '中国电信', '180': '中国电信', '181': '中国电信', '189': '中国电信',
    '177': '中国电信', '173': '中国电信', '149': '中国电信', '199': '中国电信'
}

class BotState:
    """线程安全的机器人状态管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = datetime.now()
        self.heartbeat_count = 0
        self.last_heartbeat = None
        self.message_count = 0
        self.user_data = defaultdict(lambda: {
            'first_seen': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'query_count': 0,
            'phone_numbers_found': 0,
            'queries_today': 0,
            'last_query_date': None,
            'phone_history': deque(maxlen=50),
            'hourly_stats': defaultdict(int),
            'country_stats': defaultdict(int),
            'daily_queries': defaultdict(int)
        })
        self.global_stats = {
            'total_queries': 0,
            'total_users': 0,
            'total_phone_numbers': 0,
            'start_time': self.start_time.isoformat(),
            'hourly_distribution': defaultdict(int),
            'country_distribution': defaultdict(int),
            'daily_stats': defaultdict(int)
        }
        
        # 启动心跳线程
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_worker, daemon=True)
        self.heartbeat_thread.start()
        print("✅ 机器人状态管理系统启动")

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

    def record_query(self, user_id, phone_numbers_found=0, countries=None):
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
                
                # 记录国家统计
                if countries:
                    for country in countries:
                        user_data['country_stats'][country] += 1
                        self.global_stats['country_distribution'][country] += 1
                
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
            return dict(self.global_stats)

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
                'memory_usage': f"{len(str(self.user_data))} bytes"
            }

# 全局状态管理
bot_state = BotState()

def clean_phone_number(text):
    """清理和提取电话号码"""
    if not text:
        return []
    
    # 移除所有非数字和'+/'字符
    text = re.sub(r'[^\d+\s\-().]', ' ', text)
    
    # 多种电话号码格式的正则表达式
    patterns = [
        r'\+\d{1,4}[\s\-]?\d{1,4}[\s\-]?\d{1,4}[\s\-]?\d{1,15}',  # 国际格式
        r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b',                     # 美国格式
        r'\b\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b',                     # 中国手机
        r'\b\d{2,4}[\s\-]?\d{4}[\s\-]?\d{4,8}\b',                 # 通用格式
        r'\b\d{10,15}\b'                                           # 纯数字
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
        
        # 验证长度
        if 7 <= len(clean_num) <= 15:
            cleaned_numbers.append(clean_num)
    
    return list(set(cleaned_numbers))  # 去重

def format_phone_analysis(phone_number):
    """分析电话号码详细信息"""
    analysis = {
        'original': phone_number,
        'cleaned': phone_number,
        'country_code': None,
        'country_name': '未知',
        'country_flag': '🌍',
        'number_type': '未知',
        'carrier': '未知',
        'timezone': '未知',
        'is_valid': False,
        'formatted': phone_number,
        'local_format': phone_number
    }
    
    try:
        # 处理国际格式
        if phone_number.startswith('+'):
            phone_number = phone_number[1:]
        
        # 检测国家代码
        for code in sorted(COUNTRIES_DB.keys(), key=len, reverse=True):
            if phone_number.startswith(code):
                country_info = COUNTRIES_DB[code]
                local_number = phone_number[len(code):]
                
                analysis.update({
                    'country_code': f'+{code}',
                    'country_name': country_info['name'],
                    'country_flag': COUNTRY_FLAGS.get(code, '🌍'),
                    'timezone': country_info['timezone'],
                    'local_number': local_number,
                    'formatted': f"+{code} {local_number}",
                    'is_valid': len(local_number) in country_info['mobile_length']
                })
                
                # 检查是否为手机号
                for prefix in country_info['mobile_prefixes']:
                    if local_number.startswith(prefix):
                        analysis['number_type'] = '手机号码'
                        break
                else:
                    analysis['number_type'] = '固定电话'
                
                # 中国运营商识别
                if code == '86' and len(local_number) >= 3:
                    carrier_prefix = local_number[:3]
                    analysis['carrier'] = CHINA_CARRIERS.get(carrier_prefix, '未知运营商')
                
                break
        
        # 美国/加拿大特殊处理
        if not analysis['country_code'] and len(phone_number) == 10:
            analysis.update({
                'country_code': '+1',
                'country_name': '美国/加拿大',
                'country_flag': '🇺🇸',
                'number_type': '手机号码',
                'timezone': 'UTC-5/-8',
                'formatted': f"+1 {phone_number}",
                'is_valid': True
            })
        
        # 中国手机号特殊处理
        elif not analysis['country_code'] and len(phone_number) == 11 and phone_number.startswith('1'):
            carrier_prefix = phone_number[:3]
            analysis.update({
                'country_code': '+86',
                'country_name': '中国',
                'country_flag': '🇨🇳',
                'number_type': '手机号码',
                'carrier': CHINA_CARRIERS.get(carrier_prefix, '未知运营商'),
                'timezone': 'UTC+8',
                'formatted': f"+86 {phone_number}",
                'is_valid': True
            })
    
    except Exception as e:
        print(f"电话号码分析错误: {e}")
    
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
    # 记录用户访问
    bot_state.record_query(user_id)
    
    welcome_text = f"""🎉 **欢迎使用电话号码检测机器人！** 

🔍 **核心功能：**
• 📱 智能电话号码识别和解析
• 🌍 全球国家和地区识别
• 📡 运营商信息查询（支持中国三大运营商）
• 📊 详细的号码分析报告

🚀 **使用方法：**
• 直接发送包含电话号码的消息
• 支持多种格式：+86 138xxxx8888、138-xxxx-8888 等
• 可同时处理多个号码

📈 **统计功能：**
• 📊 个人查询统计和历史记录
• 🏆 详细分析报告

**支持的号码格式：**
```
+86 13812345678  (国际格式)
138-1234-5678    (横线分隔)
(138) 1234-5678  (括号格式)
13812345678      (纯数字)
```

💡 发送任何包含电话号码的消息开始使用！
输入 /help 查看更多命令。"""

    send_telegram_message(chat_id, welcome_text)

def handle_help_command(chat_id, user_id):
    """处理/help命令"""
    help_text = """📚 **使用帮助**

🔧 **可用命令：**
• `/start` - 开始使用机器人
• `/help` - 显示此帮助信息
• `/stats` - 查看个人统计信息
• `/global` - 查看全局统计
• `/status` - 查看系统状态
• `/about` - 关于机器人

📱 **支持的号码格式：**
• 国际格式：+86 13812345678
• 国内格式：138-1234-5678
• 括号格式：(138) 1234-5678
• 纯数字：13812345678

🌍 **支持的国家/地区：**
• 🇨🇳 中国（含港澳台）
• 🇺🇸 美国/加拿大
• 🇬🇧 英国、🇫🇷 法国、🇩🇪 德国
• 🇯🇵 日本、🇰🇷 韩国
• 🇸🇬 新加坡、🇮🇳 印度
• 🇦🇺 澳大利亚 等30+国家

📊 **分析内容：**
• 国家/地区识别
• 号码类型（手机/固话）
• 运营商信息（中国地区）
• 时区信息
• 号码有效性验证

💡 **使用技巧：**
• 一次可以发送多个号码
• 支持文本中混合的号码
• 自动过滤无效号码

有问题？直接发送电话号码试试看！ 🚀"""

    send_telegram_message(chat_id, help_text)

def handle_stats_command(chat_id, user_id):
    """处理/stats命令 - 用户个人统计"""
    user_data = bot_state.get_user_stats(user_id)
    
    # 基本统计
    first_seen = datetime.fromisoformat(user_data['first_seen'])
    last_seen = datetime.fromisoformat(user_data['last_seen'])
    days_active = (last_seen.date() - first_seen.date()).days + 1
    
    stats_text = f"""📊 **您的使用统计**

👤 **基本信息：**
• 首次使用：{first_seen.strftime('%Y-%m-%d %H:%M')}
• 最后使用：{last_seen.strftime('%Y-%m-%d %H:%M')}
• 活跃天数：{days_active} 天

🔍 **查询统计：**
• 总查询次数：{user_data['query_count']:,}
• 今日查询：{user_data['queries_today']}
• 发现号码：{user_data['phone_numbers_found']:,} 个
• 平均每日：{user_data['query_count']/days_active:.1f} 次

📈 **活跃时段分析：**"""

    # 时段分析
    if user_data['hourly_stats']:
        sorted_hours = sorted(user_data['hourly_stats'].items(), key=lambda x: x[1], reverse=True)
        top_hours = sorted_hours[:3]
        for hour, count in top_hours:
            time_period = "早晨" if 6 <= hour < 12 else "下午" if 12 <= hour < 18 else "晚上" if 18 <= hour < 24 else "深夜"
            stats_text += f"\n• {hour:02d}:00 ({time_period})：{count} 次"

    # 国家分析
    if user_data['country_stats']:
        stats_text += "\n\n🌍 **查询国家分布：**"
        sorted_countries = sorted(user_data['country_stats'].items(), key=lambda x: x[1], reverse=True)[:5]
        for country, count in sorted_countries:
            flag = COUNTRY_FLAGS.get(country, '🌍')
            country_name = COUNTRIES_DB.get(country, {}).get('name', '未知')
            stats_text += f"\n• {flag} {country_name}：{count} 次"

    # 最近查询历史
    if user_data['phone_history']:
        stats_text += f"\n\n📱 **最近查询记录** (共{len(user_data['phone_history'])}条)："
        recent_phones = list(user_data['phone_history'])[-5:]  # 最近5条
        for phone in recent_phones:
            stats_text += f"\n• {phone}"

    stats_text += "\n\n继续使用来获得更多统计数据！ 📈"

    send_telegram_message(chat_id, stats_text)

def handle_global_command(chat_id, user_id):
    """处理/global命令 - 全局统计"""
    global_stats = bot_state.get_global_stats()
    system_status = bot_state.get_system_status()
    
    # 运行时间计算
    start_time = datetime.fromisoformat(global_stats['start_time'])
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    global_text = f"""🌍 **全局统计信息**

⏱️ **系统状态：**
• 运行时间：{days}天 {hours}小时 {minutes}分钟
• 活跃用户：{global_stats['total_users']:,} 人
• 总查询数：{global_stats['total_queries']:,} 次
• 处理号码：{global_stats['total_phone_numbers']:,} 个
• 心跳计数：{system_status['heartbeat_count']} 次

📊 **使用热度分析：**"""

    # 热门时段
    if global_stats['hourly_distribution']:
        sorted_hours = sorted(global_stats['hourly_distribution'].items(), key=lambda x: x[1], reverse=True)[:5]
        global_text += "\n• 🔥 **热门时段：**"
        for hour, count in sorted_hours:
            time_period = "早晨" if 6 <= hour < 12 else "下午" if 12 <= hour < 18 else "晚上" if 18 <= hour < 24 else "深夜"
            global_text += f"\n  - {hour:02d}:00 ({time_period})：{count} 次"

    # 热门国家
    if global_stats['country_distribution']:
        global_text += "\n\n• 🌍 **热门国家：**"
        sorted_countries = sorted(global_stats['country_distribution'].items(), key=lambda x: x[1], reverse=True)[:10]
        for country, count in sorted_countries:
            flag = COUNTRY_FLAGS.get(country, '🌍')
            country_name = COUNTRIES_DB.get(country, {}).get('name', '未知')
            percentage = (count / global_stats['total_queries']) * 100
            global_text += f"\n  - {flag} {country_name}：{count} 次 ({percentage:.1f}%)"

    # 每日统计趋势
    if global_stats['daily_stats']:
        global_text += "\n\n📈 **最近7天趋势：**"
        recent_days = sorted(global_stats['daily_stats'].items())[-7:]
        for date, count in recent_days:
            date_obj = datetime.fromisoformat(date)
            weekday = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][date_obj.weekday()]
            global_text += f"\n• {date} ({weekday})：{count} 次"

    global_text += f"\n\n💡 平均每用户查询：{global_stats['total_queries']/max(global_stats['total_users'], 1):.1f} 次"
    global_text += f"\n🎯 平均每查询发现：{global_stats['total_phone_numbers']/max(global_stats['total_queries'], 1):.1f} 个号码"

    send_telegram_message(chat_id, global_text)

def handle_status_command(chat_id, user_id):
    """处理/status命令 - 系统状态"""
    system_status = bot_state.get_system_status()
    
    status_text = f"""🔧 **系统状态报告**

💻 **服务器信息：**
• 系统平台：{platform.system()} {platform.release()}
• Python版本：{platform.python_version()}
• 运行时间：{system_status['uptime']}

📡 **机器人状态：**
• 消息处理：{system_status['message_count']:,} 条
• 活跃用户：{system_status['active_users']:,} 人
• 内存使用：{system_status['memory_usage']}

❤️ **心跳监控：**
• 心跳次数：{system_status['heartbeat_count']} 次
• 最后心跳：{datetime.fromisoformat(system_status['last_heartbeat']).strftime('%H:%M:%S') if system_status['last_heartbeat'] else '未知'}
• 监控状态：🟢 正常

🌐 **服务状态：**
• Telegram API：🟢 连接正常
• 数据处理：🟢 正常运行  
• 统计系统：🟢 正常工作
• 自动重启：🟢 已启用

✅ 所有系统运行正常！"""

    send_telegram_message(chat_id, status_text)

def handle_about_command(chat_id, user_id):
    """处理/about命令"""
    about_text = """ℹ️ **关于本机器人**

🤖 **机器人信息：**
• 名称：电话号码检测机器人
• 版本：v10.3 简化版
• 作者：MiniMax Agent
• 架构：零依赖架构

🛠️ **技术特性：**
• 🚀 使用Python内置库开发
• 🔒 零第三方依赖，稳定可靠
• ⚡ 高性能多线程处理
• 🌍 支持全球30+国家号码识别

📊 **功能特色：**
• 智能号码提取和验证
• 详细的国家和运营商信息
• 完整的统计分析系统
• 实时系统监控

🎯 **设计理念：**
• 简单易用的用户界面
• 快速准确的号码分析
• 详细全面的统计报告
• 稳定可靠的服务质量

💡 **更新日志：**
• v10.3：移除等级系统，简化操作
• v10.2：优化统计功能和用户体验
• v10.1：增强号码识别准确度
• v10.0：全面重构，零依赖架构

🔄 本机器人持续更新优化中...

感谢使用！有任何问题请直接测试功能 🙏"""

    send_telegram_message(chat_id, about_text)

def handle_phone_message(chat_id, user_id, message_text):
    """处理包含电话号码的消息"""
    try:
        # 提取电话号码
        phone_numbers = clean_phone_number(message_text)
        
        if not phone_numbers:
            response_text = """❌ **没有检测到有效的电话号码**

💡 **支持的格式示例：**
• +86 13812345678
• 138-1234-5678  
• (138) 1234-5678
• 13812345678

请发送包含电话号码的消息！"""
            send_telegram_message(chat_id, response_text)
            return
        
        # 分析每个号码
        analyses = []
        countries_found = set()
        
        for phone in phone_numbers:
            analysis = format_phone_analysis(phone)
            analyses.append(analysis)
            if analysis['country_code']:
                country_code = analysis['country_code'].replace('+', '')
                countries_found.add(country_code)
                
            # 记录到历史
            user_data = bot_state.get_user_stats(user_id)
            user_data['phone_history'].append(analysis['formatted'])

        # 记录统计（移除了等级更新部分）
        bot_state.record_query(user_id, len(phone_numbers), list(countries_found))
        user_data = bot_state.get_user_stats(user_id)

        # 构建响应
        if len(analyses) == 1:
            # 单个号码详细分析
            analysis = analyses[0]
            response_text = f"""📱 **电话号码分析报告**

🔍 **号码信息：**
• 原始号码：`{analysis['original']}`
• 标准格式：`{analysis['formatted']}`
• 国家地区：{analysis['country_flag']} {analysis['country_name']}
• 号码类型：{analysis['number_type']}
• 运营商：{analysis['carrier']}
• 时区：{analysis['timezone']}
• 有效性：{'✅ 有效' if analysis['is_valid'] else '❌ 格式异常'}

📊 **查询统计：**
• 您的总查询：{user_data['query_count']:,} 次
• 今日查询：{user_data['queries_today']} 次
• 发现号码：{user_data['phone_numbers_found']:,} 个

感谢使用！继续发送号码获取更多分析 🚀"""

        else:
            # 多个号码批量分析
            response_text = f"""📱 **批量号码分析报告**

🔍 **共检测到 {len(analyses)} 个号码：**

"""
            
            for i, analysis in enumerate(analyses, 1):
                status = '✅' if analysis['is_valid'] else '❌'
                response_text += f"""**{i}. {analysis['formatted']}** {status}
   {analysis['country_flag']} {analysis['country_name']} | {analysis['number_type']}
   运营商：{analysis['carrier']}

"""

            response_text += f"""📊 **统计摘要：**
• 有效号码：{sum(1 for a in analyses if a['is_valid'])}/{len(analyses)}
• 涉及国家：{len(countries_found)} 个
• 您的总查询：{user_data['query_count']:,} 次

💡 发送单个号码可获取详细分析！"""

        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理电话号码消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理消息时出现错误，请稍后重试。")

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
            elif text.startswith('/about'):
                handle_about_command(chat_id, user_id)
            else:
                # 处理普通消息（可能包含电话号码）
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
                system_status = bot_state.get_system_status()
                health_data = {
                    'status': 'healthy',
                    'uptime': system_status['uptime'],
                    'message_count': system_status['message_count'],
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
    <title>电话号码检测机器人</title>
</head>
<body>
    <h1>🤖 电话号码检测机器人 v10.3</h1>
    <p>✅ 服务正在运行</p>
    <p>🚀 零依赖架构，稳定可靠</p>
    <p>📱 支持全球电话号码识别</p>
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
        
        print(f"🚀 启动电话号码检测机器人 v10.3 简化版")
        print(f"📡 服务端口: {port}")
        print(f"❤️ 心跳监控: 已启动")
        print(f"🔧 架构: 零依赖")
        
        # 启动HTTP服务器
        server = HTTPServer(('0.0.0.0', port), TelegramWebhookHandler)
        print(f"✅ 服务器启动成功，监听端口 {port}")
        
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
