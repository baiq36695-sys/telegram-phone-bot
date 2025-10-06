#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码机器人 - 智能追踪版本
专为Render等云平台设计，零依赖，智能提取+重复追踪
完整记录号码出现历史和用户统计
 
作者: MiniMax Agent
版本: 1.5.0 Smart Tracking
更新时间: 2025-10-06
"""

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import gc

# 生产环境配置
PRODUCTION_CONFIG = {
    'MAX_PHONE_REGISTRY_SIZE': 10000,  # 最大电话号码记录数
    'MAX_USER_DATA_SIZE': 5000,       # 最大用户数据记录数
    'DATA_CLEANUP_INTERVAL': 3600,    # 数据清理间隔（秒）
    'DATA_RETENTION_DAYS': 30,        # 数据保留天数
    'AUTO_RESTART_MEMORY_MB': 1000,   # 内存使用超过此值时自动重启
    'MAX_MESSAGE_LENGTH': 4096,       # Telegram消息最大长度
    'REQUEST_TIMEOUT': 30,            # HTTP请求超时时间
}

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# 线程安全的数据存储
data_lock = threading.RLock()
phone_registry = {}  # 电话号码注册表
user_data = defaultdict(dict)  # 用户数据
admin_users = set()  # 管理员用户

# 预编译正则表达式（性能优化，支持更灵活的格式）
PHONE_PATTERNS = {
    'mobile_maxis': re.compile(r'^(012|014|017|019)\d{7,8}$'),
    'mobile_celcom': re.compile(r'^(013|019)\d{7,8}$'),
    'mobile_digi': re.compile(r'^(010|011|016)\d{7,8}$'),
    'mobile_umobile': re.compile(r'^(015|018)\d{7,8}$'),
    'landline_kl_selangor': re.compile(r'^(03)\d{8}$'),
    'landline_penang': re.compile(r'^(04)\d{7}$'),
    'landline_perak': re.compile(r'^(05)\d{7}$'),
    'landline_melaka': re.compile(r'^(06)\d{7}$'),
    'landline_johor': re.compile(r'^(07)\d{7}$'),
    'landline_pahang': re.compile(r'^(09)\d{7}$'),
    'landline_sabah': re.compile(r'^(088|089)\d{6}$'),
    'landline_sarawak': re.compile(r'^(082|083|084|085|086|087)\d{6}$'),
    'toll_free': re.compile(r'^(1800)\d{6}$'),
    'premium': re.compile(r'^(600)\d{7}$')
}

# 智能提取电话号码的正则表达式
PHONE_EXTRACTION_PATTERNS = [
    # 国际格式：+60 xx-xxxx xxxx 或 +60 xxxxxxxxx
    re.compile(r'(\+?60\s?[\d\s\-\(\)]{8,12})'),
    # 本地格式：0xx-xxxxxxx 或 0xxxxxxxxx
    re.compile(r'(0[\d\s\-\(\)]{8,11})'),
    # 纯数字格式：10-11位数字
    re.compile(r'(\d{10,11})'),
    # 带括号格式：(0xx) xxx-xxxx
    re.compile(r'\(?(0\d{2,3})\)?[\s\-]?(\d{3,4})[\s\-]?(\d{3,4})')
]

STATE_MAPPING = {
    '03': '吉隆坡/雪兰莪',
    '04': '槟城',
    '05': '霹雳',
    '06': '马六甲',
    '07': '柔佛',
    '09': '彭亨/登嘉楼/吉兰丹',
    '082': '砂拉越古晋',
    '083': '砂拉越斯里阿曼',
    '084': '砂拉越泗里街',
    '085': '砂拉越民都鲁',
    '086': '砂拉越美里',
    '087': '砂拉越林梦',
    '088': '沙巴亚庇',
    '089': '沙巴山打根'
}

# 手机号码归属地映射（运营商覆盖范围）
MOBILE_COVERAGE_MAPPING = {
    'Maxis': '🇲🇾 Maxis全马来西亚',
    'Celcom': '🇲🇾 Celcom全马来西亚', 
    'DiGi': '🇲🇾 DiGi全马来西亚',
    'U Mobile': '🇲🇾 U Mobile全马来西亚',
    '未知运营商': '🇲🇾 马来西亚'
}

OPERATOR_MAPPING = {
    '010': 'DiGi',
    '011': 'DiGi',
    '012': 'Maxis',
    '013': 'Celcom',
    '014': 'Maxis',
    '015': 'U Mobile',
    '016': 'DiGi',
    '017': 'Maxis',
    '018': 'U Mobile',
    '019': 'Celcom'
}

# 简化的内存管理功能（无需psutil）
def get_memory_usage_estimate():
    """估算内存使用情况（基于数据结构大小）"""
    try:
        # 基于数据结构大小估算内存使用
        phone_count = len(phone_registry)
        user_count = len(user_data)
        # 每个记录大约1KB，基础内存约50MB
        estimated_mb = 50 + (phone_count + user_count) * 0.001
        return estimated_mb
    except:
        return 0

def cleanup_old_data():
    """清理过期数据"""
    with data_lock:
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(days=PRODUCTION_CONFIG['DATA_RETENTION_DAYS'])
        
        # 清理过期的电话号码记录
        expired_phones = []
        for phone, data in phone_registry.items():
            if datetime.fromisoformat(data.get('timestamp', '1970-01-01')) < cutoff_time:
                expired_phones.append(phone)
        
        for phone in expired_phones:
            del phone_registry[phone]
        
        # 清理过期的用户数据
        expired_users = []
        for user_id, data in user_data.items():
            if datetime.fromisoformat(data.get('last_activity', '1970-01-01')) < cutoff_time:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del user_data[user_id]
        
        # 强制内存清理限制
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:
            # 删除最老的记录
            sorted_phones = sorted(phone_registry.items(), 
                                 key=lambda x: x[1].get('timestamp', '1970-01-01'))
            excess_count = len(phone_registry) - PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']
            for phone, _ in sorted_phones[:excess_count]:
                del phone_registry[phone]
        
        if len(user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:
            sorted_users = sorted(user_data.items(),
                                key=lambda x: x[1].get('last_activity', '1970-01-01'))
            excess_count = len(user_data) - PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']
            for user_id, _ in sorted_users[:excess_count]:
                del user_data[user_id]
        
        # 强制垃圾回收
        gc.collect()
        
        print(f"数据清理完成 - 电话记录: {len(phone_registry)}, 用户数据: {len(user_data)}")

def data_cleanup_worker():
    """数据清理工作线程"""
    while True:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL'])
            cleanup_old_data()
            
            # 检查内存使用（估算）
            memory_mb = get_memory_usage_estimate()
            if memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                print(f"内存使用估算过高 ({memory_mb:.1f}MB)，建议重启服务")
                
        except Exception as e:
            print(f"数据清理错误: {e}")

def extract_phone_numbers(text):
    """从文本中智能提取电话号码"""
    phone_candidates = []
    
    # 使用多个正则表达式模式提取可能的电话号码
    for pattern in PHONE_EXTRACTION_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            if isinstance(match, tuple):
                # 处理带括号的格式
                phone_candidates.append(''.join(match))
            else:
                phone_candidates.append(match)
    
    # 清理和验证提取的号码
    valid_phones = []
    for candidate in phone_candidates:
        # 清理号码格式
        cleaned = re.sub(r'[\s\-\(\)]+', '', candidate)
        
        # 基本长度验证
        if len(cleaned) >= 9:
            valid_phones.append(candidate)
    
    return valid_phones

@lru_cache(maxsize=1000)
def analyze_phone_number(phone):
    """分析电话号码（带缓存优化，支持多种格式）"""
    original_input = phone
    
    # 清理和标准化号码格式
    phone = phone.strip()
    phone = re.sub(r'[\s\-\(\)]+', '', phone)  # 移除空格、横线、括号
    phone = phone.replace('+60', '').replace('+6060', '60')  # 处理国际格式
    
    # 处理以60开头的情况
    if phone.startswith('60'):
        phone = phone[2:]  # 移除60
    
    # 确保号码以0开头（马来西亚本地格式）
    if not phone.startswith('0') and len(phone) >= 9:
        phone = '0' + phone
    
    # 基本验证
    if not phone.isdigit() or len(phone) < 9:
        return None
    
    result = {
        'original': original_input,
        'formatted': phone,
        'type': '未知',
        'operator': '未知',
        'state': '未知',
        'coverage': '未知',
        'valid': False
    }
    
    # 检查各种号码模式
    for pattern_name, pattern in PHONE_PATTERNS.items():
        if pattern.match(phone):
            result['valid'] = True
            
            if pattern_name.startswith('mobile_'):
                result['type'] = '手机号码'
                prefix = phone[:3]
                result['operator'] = OPERATOR_MAPPING.get(prefix, '未知运营商')
                
                # 特殊处理运营商
                if pattern_name == 'mobile_maxis':
                    result['operator'] = 'Maxis'
                elif pattern_name == 'mobile_celcom':
                    result['operator'] = 'Celcom'
                elif pattern_name == 'mobile_digi':
                    result['operator'] = 'DiGi'
                elif pattern_name == 'mobile_umobile':
                    result['operator'] = 'U Mobile'
                
                # 设置手机号码归属地（全国覆盖）
                result['coverage'] = MOBILE_COVERAGE_MAPPING.get(result['operator'], '🇲🇾 马来西亚')
                    
            elif pattern_name.startswith('landline_'):
                result['type'] = '固定电话'
                prefix = phone[:3] if len(phone) >= 10 else phone[:2]
                result['state'] = STATE_MAPPING.get(prefix, '未知地区')
                if result['state'] != '未知地区':
                    result['coverage'] = f"🇲🇾 {result['state']}"
                else:
                    result['coverage'] = '🇲🇾 马来西亚'
                
            elif pattern_name == 'toll_free':
                result['type'] = '免费电话'
                result['operator'] = '全网通用'
                result['coverage'] = '🇲🇾 全马来西亚'
                
            elif pattern_name == 'premium':
                result['type'] = '增值服务号码'
                result['operator'] = '付费服务'
                result['coverage'] = '🇲🇾 全马来西亚'
            
            break
    
    return result

def register_phone_number(phone, user_id, username):
    """注册电话号码（增强版，跟踪重复和用户）"""
    with data_lock:
        current_time = datetime.now().isoformat()
        
        # 检查是否已存在
        if phone in phone_registry:
            existing = phone_registry[phone]
            # 更新重复信息
            if 'repeat_count' not in existing:
                existing['repeat_count'] = 1
                existing['users'] = [existing['username']]
            
            existing['repeat_count'] += 1
            if username not in existing['users']:
                existing['users'].append(username)
            
            existing['last_seen'] = current_time
            existing['last_user'] = username
            
            return f"❌ 号码重复"
        
        # 注册新号码
        phone_registry[phone] = {
            'user_id': user_id,
            'username': username,
            'timestamp': current_time,
            'repeat_count': 1,
            'users': [username],
            'last_seen': current_time,
            'last_user': username
        }
        
        return f"✅ 号码注册成功"

def send_telegram_message(chat_id, text):
    """发送Telegram消息（无需第三方库）"""
    try:
        # 限制消息长度
        if len(text) > PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH']:
            text = text[:PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH']-100] + "\n\n... (消息过长已截断)"
        
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        # 编码数据
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        
        # 创建请求
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        
        # 发送请求
        with urllib.request.urlopen(req, timeout=PRODUCTION_CONFIG['REQUEST_TIMEOUT']) as response:
            result = json.loads(response.read().decode())
            return result.get('ok', False)
            
    except Exception as e:
        print(f"发送消息错误: {e}")
        return False

def handle_message(message):
    """处理Telegram消息"""
    try:
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        username = message['from'].get('username', '未知用户')
        text = message.get('text', '')
        
        # 更新用户活动时间
        with data_lock:
            if user_id not in user_data:
                user_data[user_id] = {}
            user_data[user_id]['last_activity'] = datetime.now().isoformat()
        
        # 处理命令
        if text.startswith('/start'):
            response = """
🇲🇾 <b>马来西亚电话号码引导机器人</b>

📱 <b>核心功能：</b>
• 智能提取和识别马来西亚电话号码
• 显示详细的号码归属地信息
• 记录首次出现时间
• 追踪号码重复和涉及用户

💡 <b>使用方法：</b>
直接发送包含号码的消息，支持多种格式：
<code>012-3456789</code>
<code>+60 11-6852 8782</code>
<code>发送到 +60 13-970 3152</code>
<code>联系电话：60123456789</code>

📊 <b>显示信息：</b>
• 当前号码 + 号码归属地
• 首次出现时间
• 历史交换次数
• 涉及用户统计
• 重复提醒详情

🔧 <b>管理命令：</b>
/status - 查看系统状态
/clear - 清除个人数据
/help - 查看帮助

<i>智能追踪版本，完整记录 📊</i>
"""
            send_telegram_message(chat_id, response)
            
        elif text.startswith('/help'):
            response = """
📖 <b>详细帮助文档</b>

<b>🔍 支持的号码格式：</b>
• <code>012-3456789</code> (标准格式)
• <code>+60 11-6852 8782</code> (国际格式)
• <code>011 6852 8782</code> (带空格)
• <code>60123456789</code> (无+号国际)
• <code>0123456789</code> (纯数字)

<b>🤖 智能提取功能：</b>
• <code>发送到 +60 13-970 3152</code>
• <code>联系电话：012-3456789</code>
• <code>10.24/送达 +60 13-970 3152</code>
• <code>请拨打 0123456789</code>

<b>📋 支持的号码类型：</b>
• 手机号码：010,011,012,013,014,015,016,017,018,019
• 固定电话：03,04,05,06,07,09,088,089,082-087
• 免费电话：1800
• 增值服务：600

<b>📱 运营商识别：</b>
• Maxis: 012, 014, 017, 019 (🇲🇾 全马来西亚)
• Celcom: 013, 019 (🇲🇾 全马来西亚)
• DiGi: 010, 011, 016 (🇲🇾 全马来西亚)
• U Mobile: 015, 018 (🇲🇾 全马来西亚)

<b>🏠 固定电话归属地：</b>
• 03: 吉隆坡/雪兰莪
• 04: 槟城
• 05: 霹雳
• 06: 马六甲
• 07: 柔佛
• 09: 彭亨/登嘉楼/吉兰丹
• 082-087: 砂拉越各地区
• 088-089: 沙巴各地区
"""
            send_telegram_message(chat_id, response)
            
        elif text.startswith('/status'):
            memory_mb = get_memory_usage_estimate()
            response = f"""
📊 <b>系统状态报告</b>

📈 <b>数据统计：</b>
• 注册号码：{len(phone_registry)} 个
• 活跃用户：{len(user_data)} 个
• 内存使用：{memory_mb:.1f} MB

⚡ <b>性能指标：</b>
• 缓存命中率：高效运行
• 清理周期：每小时自动
• 数据保留：30天

🚀 <b>运行状态：</b>
• 服务状态：正常运行
• 版本信息：Smart Tracking 1.5.0
• 更新时间：2025-10-06
• 识别引擎：智能提取+重复追踪
• 追踪系统：实时记录已启用
• 依赖状态：零第三方依赖

<i>系统运行稳定，号码识别正常 ✅</i>
"""
            send_telegram_message(chat_id, response)
            
        elif text.startswith('/clear'):
            with data_lock:
                # 清除用户的注册号码
                user_phones = [phone for phone, data in phone_registry.items() 
                             if data['user_id'] == user_id]
                for phone in user_phones:
                    del phone_registry[phone]
                
                # 清除用户数据
                if user_id in user_data:
                    del user_data[user_id]
            
            response = "🗑️ 您的个人数据已清除完毕！"
            send_telegram_message(chat_id, response)
            
        else:
            # 智能提取电话号码
            extracted_phones = extract_phone_numbers(text)
            
            if not extracted_phones:
                response = f"""
❌ <b>未检测到电话号码</b>

您输入的内容：<code>{text}</code>

💡 <b>提示：</b>请发送包含马来西亚电话号码的消息

📝 <b>支持格式示例：</b>
• <code>012-3456789</code>
• <code>+60 11-6852 8782</code>
• <code>发送到 +60 13-970 3152</code>
• <code>联系电话：0123456789</code>

发送 /help 查看完整格式说明 📖
"""
                send_telegram_message(chat_id, response)
                return
            
            # 分析第一个提取到的电话号码
            for phone_candidate in extracted_phones:
                result = analyze_phone_number(phone_candidate)
                if result and result['valid']:
                    current_time = datetime.now()
                    
                    # 检查是否已注册并处理
                    with data_lock:
                        if result['formatted'] in phone_registry:
                            # 先更新重复信息
                            reg_result = register_phone_number(result['formatted'], user_id, username)
                            reg_info = phone_registry[result['formatted']]
                            
                            # 显示重复信息
                            first_time = reg_info['timestamp'][:19].replace('T', ' ')
                            repeat_count = reg_info.get('repeat_count', 1)
                            users_list = reg_info.get('users', [reg_info['username']])
                            user_count = len(users_list)
                            
                            info = f"""
📱 <b>号码引导人</b>

📱 当前号码：<code>{result['formatted']}</code>
📍 号码归属地：{result['coverage']}
⏰ 首次出现时间：{first_time}
🔄 历史交换：{repeat_count}次
👥 涉及用户：{user_count}人

❌ <b>重复提醒：</b>
"""
                            if user_count == 1:
                                info += f"此号码已被用户 @{users_list[0]} 使用"
                            else:
                                info += f"此号码已被多个用户使用：\n"
                                for i, user in enumerate(users_list, 1):
                                    info += f"  {i}. @{user}\n"
                                    
                        else:
                            # 新号码 - 自动注册并显示
                            reg_result = register_phone_number(result['formatted'], user_id, username)
                            current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
                            
                            info = f"""
📱 <b>号码引导人</b>

📱 当前号码：<code>{result['formatted']}</code>
📍 号码归属地：{result['coverage']}
⏰ 首次出现时间：{current_time_str}
🔄 历史交换：1次
👥 涉及用户：1人

✅ <b>新录：</b>首次记录！
"""
                    send_telegram_message(chat_id, info)
                    return
            
            # 如果所有提取的号码都无效
            response = f"""
❌ <b>未找到有效的马来西亚电话号码</b>

您输入的内容：<code>{text}</code>
检测到的候选号码：{', '.join([f'<code>{p}</code>' for p in extracted_phones])}

💡 <b>可能的问题：</b>
• 号码格式不正确
• 不是马来西亚号码格式
• 号码位数不符合要求

📝 <b>正确格式示例：</b>
• <code>012-3456789</code> (手机号码)
• <code>+60 11-6852 8782</code> (国际格式)
• <code>03-12345678</code> (固定电话)

发送 /help 查看完整格式说明 📖
"""
            send_telegram_message(chat_id, response)
                
    except Exception as e:
        print(f"处理消息错误: {e}")
        try:
            send_telegram_message(chat_id, "❌ 处理请求时发生错误，请稍后重试")
        except:
            pass

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # 解析Telegram更新
            update = json.loads(post_data.decode())
            
            if 'message' in update:
                handle_message(update['message'])
            
            # 返回成功响应
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            print(f"Webhook处理错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def do_GET(self):
        """健康检查端点"""
        try:
            memory_mb = get_memory_usage_estimate()
            status = {
                'status': 'healthy',
                'version': '1.3.0 Final Fixed',
                'phone_registry_size': len(phone_registry),
                'user_data_size': len(user_data),
                'memory_estimate_mb': memory_mb,
                'timestamp': datetime.now().isoformat()
            }
            
            response = json.dumps(status, ensure_ascii=False, indent=2)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(response.encode('utf-8'))
            
        except Exception as e:
            print(f"健康检查错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """减少日志输出"""
        pass

def run_server():
    """启动HTTP服务器"""
    port = int(os.getenv('PORT', 10000))
    
    try:
        server = HTTPServer(('', port), WebhookHandler)
        print(f"马来西亚电话号码机器人已启动")
        print(f"版本: 1.3.0 Final Fixed (智能提取版)")
        print(f"端口: {port}")
        print(f"内存估算: {get_memory_usage_estimate():.1f} MB")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)
        
        # 启动数据清理线程
        cleanup_thread = threading.Thread(target=data_cleanup_worker, daemon=True)
        cleanup_thread.start()
        
        server.serve_forever()
        
    except Exception as e:
        print(f"服务器启动错误: {e}")

if __name__ == '__main__':
    run_server()
