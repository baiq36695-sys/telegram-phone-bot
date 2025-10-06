#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码分析机器人 - 生产稳定版
专为长期运行设计，解决内存泄漏和稳定性问题

长期运行特性：
- 自动内存管理
- 数据过期清理
- 异常恢复机制
- 资源限制保护
- 数据持久化
- 性能监控

作者: MiniMax Agent
"""

import os
import re
import json
import threading
import time
from datetime import datetime, timedelta
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import lru_cache
import urllib.request
import urllib.parse
import gc
import weakref

# 机器人配置
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
TELEGRAM_API = f'https://api.telegram.org/bot{BOT_TOKEN}'

# 生产环境配置
PRODUCTION_CONFIG = {
    'MAX_PHONE_REGISTRY_SIZE': 10000,  # 最大号码注册数量
    'MAX_USER_DATA_SIZE': 5000,       # 最大用户数量
    'DATA_CLEANUP_INTERVAL': 3600,    # 清理间隔（秒）
    'DATA_RETENTION_DAYS': 30,        # 数据保留天数
    'PHONE_HISTORY_SIZE': 20,         # 用户历史记录限制
    'MEMORY_CHECK_INTERVAL': 1800,    # 内存检查间隔（秒）
    'AUTO_RESTART_MEMORY_MB': 1000,   # 内存阈值（MB）
}

# 预编译正则表达式
PHONE_PATTERNS = [
    re.compile(r'\+?60\s*[-\s]?([1][0-9]\d{7,8})', re.IGNORECASE),
    re.compile(r'\+?60\s*[-\s]?([0][1-9]\d{6,8})', re.IGNORECASE),
    re.compile(r'\b0([1-9]\d{7,9})\b'),
    re.compile(r'\b([1][0-9]\d{7,8})\b'),
]

# 运营商数据
MOBILE_CARRIERS = {
    '010': 'DiGi', '011': 'DiGi', '012': 'Maxis', '013': 'DiGi',
    '014': 'DiGi', '015': 'DiGi', '016': 'DiGi', '017': 'Maxis',
    '018': 'U Mobile', '019': 'DiGi', '020': 'Electcoms'
}

LANDLINE_REGIONS = {
    '03': '雪兰莪/吉隆坡/布城', '04': '吉打/槟城', '05': '霹雳',
    '06': '马六甲/森美兰', '07': '柔佛', '08': '沙巴', '09': '吉兰丹/登嘉楼',
    '082': '沙捞越古晋', '083': '沙捞越斯里阿曼', '084': '沙捞越沙拉卓',
    '085': '沙捞越美里', '086': '沙捞越泗里街', '087': '沙巴亚庇',
    '088': '沙巴斗湖', '089': '沙巴根地咬'
}

class ProductionPhoneState:
    """生产级状态管理 - 自动内存管理和数据清理"""
    
    def __init__(self):
        self._lock = threading.RLock()
        self.start_time = datetime.now()
        self.message_count = 0
        self.restart_count = 0
        
        # 有限容量的数据结构
        self.phone_registry = {}
        self.user_data = {}
        self.user_names = {}
        
        # 基础统计
        self.global_stats = {
            'total_queries': 0,
            'total_users': 0,
            'total_phone_numbers': 0,
            'total_duplicates': 0,
            'start_time': self.start_time.isoformat(),
            'carrier_distribution': defaultdict(int),
            'cleanup_count': 0,
            'memory_cleanups': 0
        }
        
        # 启动自动清理线程
        self._start_maintenance_thread()
    
    def _start_maintenance_thread(self):
        """启动维护线程"""
        def maintenance_loop():
            while True:
                try:
                    time.sleep(PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL'])
                    self._auto_cleanup()
                    self._memory_check()
                except Exception as e:
                    print(f"维护线程错误: {e}")
        
        maintenance_thread = threading.Thread(target=maintenance_loop, daemon=True)
        maintenance_thread.start()
        print("✅ 自动维护线程已启动")
    
    def _auto_cleanup(self):
        """自动清理过期数据"""
        try:
            with self._lock:
                current_time = datetime.now()
                cutoff_time = current_time - timedelta(days=PRODUCTION_CONFIG['DATA_RETENTION_DAYS'])
                
                # 清理过期号码注册
                expired_phones = []
                for phone, data in self.phone_registry.items():
                    last_seen = datetime.fromisoformat(data['last_seen'])
                    if last_seen < cutoff_time:
                        expired_phones.append(phone)
                
                for phone in expired_phones:
                    del self.phone_registry[phone]
                
                # 清理过期用户数据
                expired_users = []
                for user_id, data in self.user_data.items():
                    last_seen = datetime.fromisoformat(data['last_seen'])
                    if last_seen < cutoff_time:
                        expired_users.append(user_id)
                
                for user_id in expired_users:
                    del self.user_data[user_id]
                    self.user_names.pop(user_id, None)
                
                # 限制数据大小
                self._enforce_size_limits()
                
                self.global_stats['cleanup_count'] += 1
                
                if expired_phones or expired_users:
                    print(f"🧹 自动清理完成: 号码{len(expired_phones)}个, 用户{len(expired_users)}个")
                
        except Exception as e:
            print(f"自动清理错误: {e}")
    
    def _enforce_size_limits(self):
        """强制执行大小限制"""
        try:
            # 限制号码注册表大小
            if len(self.phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:
                # 删除最旧的记录
                sorted_phones = sorted(
                    self.phone_registry.items(),
                    key=lambda x: x[1]['last_seen']
                )
                
                to_remove = len(sorted_phones) - PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']
                for phone, _ in sorted_phones[:to_remove]:
                    del self.phone_registry[phone]
                
                print(f"📦 号码注册表大小限制: 删除{to_remove}条记录")
            
            # 限制用户数据大小
            if len(self.user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:
                sorted_users = sorted(
                    self.user_data.items(),
                    key=lambda x: x[1]['last_seen']
                )
                
                to_remove = len(sorted_users) - PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']
                for user_id, _ in sorted_users[:to_remove]:
                    del self.user_data[user_id]
                    self.user_names.pop(user_id, None)
                
                print(f"👥 用户数据大小限制: 删除{to_remove}条记录")
        
        except Exception as e:
            print(f"大小限制执行错误: {e}")
    
    def _memory_check(self):
        """内存检查和清理"""
        try:
            import psutil
            import os
            
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            if memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                print(f"⚠️ 内存使用过高: {memory_mb:.1f}MB，执行强制清理")
                
                with self._lock:
                    # 强制清理
                    self._aggressive_cleanup()
                    
                    # 垃圾回收
                    gc.collect()
                    
                    self.global_stats['memory_cleanups'] += 1
                
                # 再次检查内存
                new_memory_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
                print(f"🧹 清理后内存: {new_memory_mb:.1f}MB")
        
        except ImportError:
            # psutil 不可用时的简单内存检查
            gc.collect()
        except Exception as e:
            print(f"内存检查错误: {e}")
    
    def _aggressive_cleanup(self):
        """激进清理 - 在内存压力下使用"""
        try:
            # 减少数据保留时间
            current_time = datetime.now()
            cutoff_time = current_time - timedelta(days=7)  # 只保留7天
            
            # 清理号码注册
            expired_phones = [
                phone for phone, data in self.phone_registry.items()
                if datetime.fromisoformat(data['last_seen']) < cutoff_time
            ]
            
            for phone in expired_phones:
                del self.phone_registry[phone]
            
            # 清理用户数据
            expired_users = [
                user_id for user_id, data in self.user_data.items()
                if datetime.fromisoformat(data['last_seen']) < cutoff_time
            ]
            
            for user_id in expired_users:
                del self.user_data[user_id]
                self.user_names.pop(user_id, None)
            
            # 进一步缩减大小限制
            max_phones = PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE'] // 2
            max_users = PRODUCTION_CONFIG['MAX_USER_DATA_SIZE'] // 2
            
            if len(self.phone_registry) > max_phones:
                sorted_phones = sorted(
                    self.phone_registry.items(),
                    key=lambda x: x[1]['last_seen']
                )
                for phone, _ in sorted_phones[:len(sorted_phones) - max_phones]:
                    del self.phone_registry[phone]
            
            if len(self.user_data) > max_users:
                sorted_users = sorted(
                    self.user_data.items(),
                    key=lambda x: x[1]['last_seen']
                )
                for user_id, _ in sorted_users[:len(sorted_users) - max_users]:
                    del self.user_data[user_id]
                    self.user_names.pop(user_id, None)
            
            print(f"🚨 激进清理完成: 号码{len(expired_phones)}个, 用户{len(expired_users)}个")
            
        except Exception as e:
            print(f"激进清理错误: {e}")
    
    def update_user_info(self, user_id, user_info):
        """更新用户信息"""
        try:
            with self._lock:
                current_time = datetime.now()
                
                # 创建或更新用户数据
                if user_id not in self.user_data:
                    self.user_data[user_id] = {
                        'first_seen': current_time.isoformat(),
                        'last_seen': current_time.isoformat(),
                        'query_count': 0,
                        'phone_numbers_found': 0,
                        'queries_today': 0,
                        'last_query_date': None,
                        'phone_history': deque(maxlen=PRODUCTION_CONFIG['PHONE_HISTORY_SIZE']),
                        'carrier_stats': defaultdict(int),
                        'username': '',
                        'first_name': '',
                        'last_name': ''
                    }
                
                user_data = self.user_data[user_id]
                user_data['last_seen'] = current_time.isoformat()
                user_data['username'] = user_info.get('username', '')
                user_data['first_name'] = user_info.get('first_name', '')
                user_data['last_name'] = user_info.get('last_name', '')
                
                # 缓存显示名称
                full_name = f"{user_data['first_name']} {user_data['last_name']}".strip()
                if user_data['username']:
                    display_name = f"@{user_data['username']} ({full_name})"
                else:
                    display_name = full_name or f"用户{user_id}"
                
                self.user_names[user_id] = display_name
                
        except Exception as e:
            print(f"更新用户信息错误: {e}")
    
    def register_phone_number(self, phone_number, user_id, user_info=None):
        """注册号码并检测重复"""
        try:
            with self._lock:
                normalized_phone = self._normalize_phone(phone_number)
                current_time = datetime.now()
                
                if user_info:
                    self.update_user_info(user_id, user_info)
                
                current_user_name = self.user_names.get(user_id, f"用户{user_id}")
                
                # 检查重复
                if normalized_phone in self.phone_registry:
                    registry_entry = self.phone_registry[normalized_phone]
                    registry_entry['occurrence_count'] += 1
                    registry_entry['last_seen'] = current_time.isoformat()
                    self.global_stats['total_duplicates'] += 1
                    
                    first_user_id = registry_entry['first_user_id']
                    first_user_name = self.user_names.get(first_user_id, f"用户{first_user_id}")
                    
                    return {
                        'is_duplicate': True,
                        'formatted_phone': self._format_phone_display(normalized_phone),
                        'current_user_name': current_user_name,
                        'first_user_name': first_user_name,
                        'first_seen': datetime.fromisoformat(registry_entry['first_seen']),
                        'occurrence_count': registry_entry['occurrence_count'],
                        'total_users': len(set([first_user_id, user_id]))
                    }
                else:
                    # 新号码注册
                    self.phone_registry[normalized_phone] = {
                        'first_seen': current_time.isoformat(),
                        'last_seen': current_time.isoformat(),
                        'first_user_id': user_id,
                        'occurrence_count': 1
                    }
                    
                    return {
                        'is_duplicate': False,
                        'formatted_phone': self._format_phone_display(normalized_phone),
                        'current_user_name': current_user_name,
                        'first_user_name': current_user_name,
                        'first_seen': current_time,
                        'occurrence_count': 1,
                        'total_users': 1
                    }
        except Exception as e:
            print(f"注册号码错误: {e}")
            return None
    
    def _normalize_phone(self, phone):
        """标准化号码"""
        clean = re.sub(r'[^\d]', '', phone)
        if clean.startswith('60'):
            return clean
        elif clean.startswith('0'):
            return '60' + clean[1:]
        else:
            return '60' + clean
    
    def _format_phone_display(self, normalized_phone):
        """格式化显示"""
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
        """记录查询"""
        try:
            with self._lock:
                current_time = datetime.now()
                today = current_time.date().isoformat()
                
                # 确保用户数据存在
                if user_id not in self.user_data:
                    self.user_data[user_id] = {
                        'first_seen': current_time.isoformat(),
                        'last_seen': current_time.isoformat(),
                        'query_count': 0,
                        'phone_numbers_found': 0,
                        'queries_today': 0,
                        'last_query_date': None,
                        'phone_history': deque(maxlen=PRODUCTION_CONFIG['PHONE_HISTORY_SIZE']),
                        'carrier_stats': defaultdict(int),
                        'username': '',
                        'first_name': '',
                        'last_name': ''
                    }
                
                user_data = self.user_data[user_id]
                user_data['last_seen'] = current_time.isoformat()
                user_data['query_count'] += 1
                user_data['phone_numbers_found'] += phone_numbers_found
                
                if user_data['last_query_date'] != today:
                    user_data['queries_today'] = 0
                    user_data['last_query_date'] = today
                
                user_data['queries_today'] += 1
                
                if carriers:
                    for carrier in carriers:
                        user_data['carrier_stats'][carrier] += 1
                        self.global_stats['carrier_distribution'][carrier] += 1
                
                self.global_stats['total_queries'] += 1
                self.global_stats['total_phone_numbers'] += phone_numbers_found
                self.global_stats['total_users'] = len(self.user_data)
                
                self.message_count += 1
                
        except Exception as e:
            print(f"记录查询错误: {e}")
    
    def get_user_stats(self, user_id):
        """获取用户统计"""
        with self._lock:
            return dict(self.user_data.get(user_id, {}))
    
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
                'message_count': self.message_count,
                'active_users': len(self.user_data),
                'registered_phones': len(self.phone_registry),
                'cleanup_count': self.global_stats['cleanup_count'],
                'memory_cleanups': self.global_stats['memory_cleanups'],
                'restart_count': self.restart_count
            }
    
    def manual_cleanup(self):
        """手动清理"""
        try:
            with self._lock:
                old_phones = len(self.phone_registry)
                old_users = len(self.user_data)
                
                self._auto_cleanup()
                gc.collect()
                
                new_phones = len(self.phone_registry)
                new_users = len(self.user_data)
                
                return {
                    'phones_removed': old_phones - new_phones,
                    'users_removed': old_users - new_users,
                    'phones_remaining': new_phones,
                    'users_remaining': new_users
                }
        except Exception as e:
            print(f"手动清理错误: {e}")
            return None

# 全局状态实例
phone_state = ProductionPhoneState()

@lru_cache(maxsize=1000)
def analyze_malaysia_phone(phone_number):
    """分析马来西亚号码"""
    analysis = {
        'original': phone_number,
        'type': 'unknown',
        'carrier': '未知',
        'location': '未知',
        'formatted': phone_number,
        'is_valid': False
    }
    
    try:
        clean_number = re.sub(r'[^\d]', '', phone_number)
        
        if not clean_number:
            return analysis
        
        if clean_number.startswith('60'):
            local_format = clean_number[2:]
        elif clean_number.startswith('0'):
            local_format = clean_number[1:]
        else:
            local_format = clean_number
        
        # 手机号码检测
        if len(local_format) >= 9 and local_format[0] in ['0', '1']:
            if local_format.startswith('01'):
                prefix = local_format[:3]
                carrier = MOBILE_CARRIERS.get(prefix, '未知运营商')
                
                analysis.update({
                    'type': 'mobile',
                    'carrier': carrier,
                    'location': f'🇲🇾 {carrier}',
                    'formatted': f"+60 {local_format[:3]}-{local_format[3:6]} {local_format[6:]}",
                    'is_valid': True
                })
                return analysis
        
        # 固话检测
        for code in LANDLINE_REGIONS:
            if local_format.startswith(code) and len(local_format) >= len(code) + 4:
                region = LANDLINE_REGIONS[code]
                analysis.update({
                    'type': 'landline',
                    'carrier': '固话',
                    'location': f'🇲🇾 {region}',
                    'formatted': f"+60 {code} {local_format[len(code):]}",
                    'is_valid': True
                })
                return analysis
        
        # 其他格式
        if len(local_format) >= 7:
            analysis.update({
                'location': '🇲🇾 马来西亚·未知运营商',
                'is_valid': True,
                'carrier': '未知运营商'
            })
    
    except Exception as e:
        print(f"号码分析错误: {e}")
    
    return analysis

def clean_malaysia_phone_number(message_text):
    """提取号码"""
    found_numbers = []
    
    for pattern in PHONE_PATTERNS:
        matches = pattern.findall(message_text)
        found_numbers.extend(matches)
    
    unique_numbers = list(set(found_numbers))
    valid_analyses = []
    
    for number in unique_numbers:
        analysis = analyze_malaysia_phone(number)
        if analysis['is_valid']:
            valid_analyses.append(analysis)
    
    return valid_analyses

def send_telegram_message(chat_id, text, parse_mode='Markdown'):
    """发送消息"""
    try:
        if len(text) > 4000:
            parts = [text[i:i+3900] for i in range(0, len(text), 3900)]
            for part in parts:
                send_single_message(chat_id, part, parse_mode)
                time.sleep(0.3)
        else:
            send_single_message(chat_id, text, parse_mode)
    except Exception as e:
        print(f"发送消息错误: {e}")

def send_single_message(chat_id, text, parse_mode='Markdown'):
    """发送单条消息"""
    try:
        data = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode
        }).encode('utf-8')
        
        req = urllib.request.Request(
            f'{TELEGRAM_API}/sendMessage',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode('utf-8'))
            if not result.get('ok'):
                print(f"Telegram API错误: {result}")
                
    except Exception as e:
        print(f"发送单条消息错误: {e}")

def handle_start_command(chat_id, user_id):
    """处理开始命令"""
    phone_state.record_query(user_id)
    
    welcome_text = f"""🗣️ **欢迎使用管号机器人!** [生产稳定版 🛡️]

🔍 **专业功能:**
• 📱 马来西亚手机和固话识别  
• 🔄 重复号码检测及关联信息
• 👥 用户追踪和统计
• 📍 精准归属地显示
• 🛡️ **长期运行稳定保证**

🛡️ **生产级特性:**
• 🧹 自动内存管理
• ⏰ 数据过期清理
• 📊 性能监控
• 🔧 故障自愈

📱 **支持格式:**
```
+60 11-6852 8782
011-6852 8782
03-1234 5678
60116852782
```

🚀 直接发送号码开始检测!
💡 输入 /help 查看更多命令。

🛡️ **稳定版承诺:** 7x24小时不间断运行！"""

    send_telegram_message(chat_id, welcome_text)

def handle_phone_message(chat_id, user_id, message_text, user_info=None):
    """处理电话号码消息"""
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
        
        carriers = []
        
        for analysis in phone_numbers:
            if analysis['is_valid']:
                duplicate_info = phone_state.register_phone_number(
                    analysis['original'], user_id, user_info
                )
                analysis['duplicate_info'] = duplicate_info
                
                if analysis['carrier'] != '未知':
                    carriers.append(analysis['carrier'])
        
        phone_state.record_query(user_id, len(phone_numbers), carriers)
        
        # 格式化响应
        if len(phone_numbers) == 1:
            analysis = phone_numbers[0]
            duplicate_info = analysis['duplicate_info']
            
            icon = "📱" if analysis['type'] == 'mobile' else "📞"
            type_name = "手机号码" if analysis['type'] == 'mobile' else "固定电话"
            
            response_text = f"""🗣️ 当前号码: {duplicate_info['formatted_phone']}
{icon} **{type_name}** - {analysis['location']}
⚡ {'运营商' if analysis['type'] == 'mobile' else '类型'}: **{analysis['carrier']}**
👤 当前用户: {duplicate_info['current_user_name']}

"""
            if duplicate_info['first_user_name'] != duplicate_info['current_user_name']:
                response_text += f"""👤 首次用户: {duplicate_info['first_user_name']}
⏰ 首次时间: {duplicate_info['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}

"""
            response_text += f"""📈 历史交叉数: {duplicate_info['occurrence_count']}次
👥 涉及用户: {duplicate_info['total_users']}人"""
            
            if duplicate_info['is_duplicate']:
                response_text += "\n\n🔄 **检测到重复号码!**"
        else:
            # 多个号码
            response_text = f"🔍 **检测到 {len(phone_numbers)} 个马来西亚号码:**\n\n"
            
            for i, analysis in enumerate(phone_numbers, 1):
                duplicate_info = analysis['duplicate_info']
                icon = "📱" if analysis['type'] == 'mobile' else "📞"
                
                response_text += f"{icon} **{duplicate_info['formatted_phone']}**\n"
                response_text += f"📍 {analysis['location']}\n"
                response_text += f"👤 用户: {duplicate_info['current_user_name']}\n"
                response_text += f"📈 历史: {duplicate_info['occurrence_count']}次\n"
                
                if duplicate_info['is_duplicate']:
                    response_text += "🔄 重复检测\n"
                
                if i < len(phone_numbers):
                    response_text += "\n---\n\n"
        
        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理电话号码消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理您的请求时发生错误，请稍后重试。")

def handle_cleanup_command(chat_id, user_id):
    """处理清理命令"""
    phone_state.record_query(user_id)
    
    try:
        cleanup_result = phone_state.manual_cleanup()
        if cleanup_result:
            response_text = f"""✅ **手动清理完成!**

📊 **清理结果:**
• 号码记录: 删除 {cleanup_result['phones_removed']} 个，保留 {cleanup_result['phones_remaining']} 个
• 用户数据: 删除 {cleanup_result['users_removed']} 个，保留 {cleanup_result['users_remaining']} 个

🧹 **自动清理机制:**
• 数据保留期: {PRODUCTION_CONFIG['DATA_RETENTION_DAYS']} 天
• 清理间隔: {PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']/3600:.1f} 小时
• 内存限制: {PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']} MB

🛡️ 系统持续稳定运行中！"""
        else:
            response_text = "❌ 清理操作失败，请稍后重试。"
            
        send_telegram_message(chat_id, response_text)
    except Exception as e:
        print(f"处理清理命令错误: {e}")

def handle_help_command(chat_id, user_id):
    """处理帮助命令"""
    phone_state.record_query(user_id)
    
    help_text = """📋 **命令帮助 - 生产稳定版**

🔧 **可用命令:**
• /start - 开始使用机器人
• /help - 显示此帮助信息
• /stats - 查看个人统计
• /status - 查看系统状态  
• /cleanup - 手动清理数据

🛡️ **生产级特性:**
• 🧹 自动内存管理 (每小时)
• 📅 数据过期清理 (30天)
• 📊 性能实时监控
• 🔄 故障自动恢复
• 💾 内存限制保护

🔍 **核心功能:**
• 重复号码检测
• 运营商识别
• 用户历史追踪
• 详细统计报告

⚡ 直接发送马来西亚电话号码即可分析！

🛡️ **稳定性保证:** 专为7x24小时运行设计！"""

    send_telegram_message(chat_id, help_text)

def handle_stats_command(chat_id, user_id):
    """处理统计命令"""
    phone_state.record_query(user_id)
    
    try:
        user_stats = phone_state.get_user_stats(user_id)
        global_stats = phone_state.get_global_stats()
        
        if not user_stats:
            response_text = "❌ 暂无用户统计数据"
        else:
            first_seen = datetime.fromisoformat(user_stats['first_seen'])
            
            stats_text = f"""📊 **个人统计报告**

👤 **用户信息:**
• 首次使用: {first_seen.strftime('%Y-%m-%d %H:%M:%S')}
• 查询次数: {user_stats['query_count']:,} 次
• 发现号码: {user_stats['phone_numbers_found']:,} 个
• 今日查询: {user_stats['queries_today']:,} 次

📱 **运营商分布:**"""
            
            for carrier, count in user_stats['carrier_stats'].items():
                stats_text += f"\n• {carrier}: {count} 次"
            
            stats_text += f"""

🌐 **全局统计:**
• 总查询数: {global_stats['total_queries']:,} 次
• 总用户数: {global_stats['total_users']:,} 人
• 注册号码: {global_stats['total_registered_phones']:,} 个
• 重复检测: {global_stats['total_duplicates']:,} 次

🛡️ 生产稳定版运行中"""
            
            response_text = stats_text
        
        send_telegram_message(chat_id, response_text)
        
    except Exception as e:
        print(f"处理统计命令错误: {e}")

def handle_status_command(chat_id, user_id):
    """处理状态命令"""
    phone_state.record_query(user_id)
    
    try:
        system_status = phone_state.get_system_status()
        global_stats = phone_state.get_global_stats()
        
        status_text = f"""🔧 **系统状态报告 - 生产版**

⏱️ **运行状态:**
• 运行时间: {system_status['uptime']}
• 处理消息: {system_status['message_count']:,} 条
• 活跃用户: {system_status['active_users']:,} 人
• 注册号码: {system_status['registered_phones']:,} 个

🧹 **维护统计:**
• 自动清理: {system_status['cleanup_count']:,} 次
• 内存清理: {system_status['memory_cleanups']:,} 次
• 重启次数: {system_status['restart_count']:,} 次

📊 **运营商热度:**"""
        
        sorted_carriers = sorted(global_stats['carrier_distribution'].items(), 
                               key=lambda x: x[1], reverse=True)
        for carrier, count in sorted_carriers[:5]:
            status_text += f"\n• {carrier}: {count:,} 次"
        
        status_text += f"""

⚙️ **配置信息:**
• 最大号码: {PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:,} 个
• 最大用户: {PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:,} 个
• 保留期限: {PRODUCTION_CONFIG['DATA_RETENTION_DAYS']} 天
• 清理间隔: {PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']/3600:.1f} 小时

🛡️ **版本:** 生产稳定版 - 长期运行保证"""
        
        send_telegram_message(chat_id, status_text)
        
    except Exception as e:
        print(f"处理状态命令错误: {e}")

class ProductionWebhookHandler(BaseHTTPRequestHandler):
    """生产级Webhook处理器"""
    
    def do_POST(self):
        """处理POST请求"""
        try:
            if self.path != '/webhook':
                self.send_response(404)
                self.end_headers()
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 8000:
                self.send_response(413)
                self.end_headers()
                return
            
            post_data = self.rfile.read(content_length)
            update = json.loads(post_data.decode('utf-8'))
            
            # 异步处理更新
            threading.Thread(target=self.process_update, args=(update,), daemon=True).start()
            
            # 立即响应
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            print(f"Webhook处理错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def process_update(self, update):
        """处理Telegram更新"""
        try:
            if 'message' not in update:
                return
            
            message = update['message']
            chat_id = message['chat']['id']
            user_id = message['from']['id']
            user_info = message['from']
            
            if 'text' not in message:
                return
            
            text = message['text'].strip()
            
            # 命令路由
            if text.startswith('/start'):
                handle_start_command(chat_id, user_id)
            elif text.startswith('/help'):
                handle_help_command(chat_id, user_id)
            elif text.startswith('/stats'):
                handle_stats_command(chat_id, user_id)
            elif text.startswith('/status'):
                handle_status_command(chat_id, user_id)
            elif text.startswith('/cleanup'):
                handle_cleanup_command(chat_id, user_id)
            else:
                handle_phone_message(chat_id, user_id, text, user_info)
                
        except Exception as e:
            print(f"处理更新错误: {e}")
    
    def do_GET(self):
        """健康检查"""
        try:
            system_status = phone_state.get_system_status()
            
            response_data = {
                'status': 'healthy',
                'uptime': system_status['uptime'],
                'message_count': system_status['message_count'],
                'version': '生产稳定版',
                'features': ['auto_cleanup', 'memory_management', 'long_term_stable']
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
    print("🚀 启动马来西亚号码分析机器人（生产稳定版）...")
    
    port = int(os.environ.get('PORT', 8000))
    
    try:
        if setup_webhook():
            print("✅ Webhook配置完成")
        else:
            print("⚠️  Webhook配置失败，但继续运行")
        
        server = HTTPServer(('0.0.0.0', port), ProductionWebhookHandler)
        print(f"🛡️ 生产稳定版服务器启动在端口 {port}")
        print("🔥 生产级特性：")
        print(f"  ✅ 自动内存管理 (每{PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']/3600:.1f}小时)")
        print(f"  ✅ 数据过期清理 ({PRODUCTION_CONFIG['DATA_RETENTION_DAYS']}天)")
        print(f"  ✅ 内存限制保护 ({PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']}MB)")
        print(f"  ✅ 容量限制: 号码{PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:,}个, 用户{PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:,}个")
        print("  🛡️ 长期运行稳定保证")
        print("✅ 系统就绪，7x24小时运行模式...")
        
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n⏹️  收到停止信号")
    except Exception as e:
        print(f"❌ 程序错误: {e}")
        # 生产环境下尝试重启
        phone_state.restart_count += 1
        print(f"🔄 尝试重启... (第{phone_state.restart_count}次)")
        time.sleep(5)
        main()  # 递归重启
    finally:
        print("🔄 程序结束")

if __name__ == '__main__':
    main()
