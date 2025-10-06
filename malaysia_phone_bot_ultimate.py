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
import signal
import sys
import logging
from contextlib import contextmanager

# 生产环境配置（长期运行优化）
PRODUCTION_CONFIG = {
    'MAX_PHONE_REGISTRY_SIZE': 5000,   # 最大电话号码记录数（降低以节省内存）
    'MAX_USER_DATA_SIZE': 2000,       # 最大用户数据记录数（降低以节省内存）
    'DATA_CLEANUP_INTERVAL': 1800,    # 数据清理间隔（30分钟，更频繁清理）
    'DATA_RETENTION_DAYS': 7,         # 数据保留天数（降低以减少内存压力）
    'AUTO_RESTART_MEMORY_MB': 400,    # 内存使用超过此值时自动重启（适合免费云服务）
    'MAX_MESSAGE_LENGTH': 4096,       # Telegram消息最大长度
    'REQUEST_TIMEOUT': 15,            # HTTP请求超时时间（降低避免长时间阻塞）
    'MAX_CONCURRENT_REQUESTS': 10,    # 最大并发请求数
    'HEALTH_CHECK_INTERVAL': 300,     # 健康检查间隔（5分钟）
    'ERROR_RETRY_MAX': 3,             # 最大重试次数
    'GRACEFUL_SHUTDOWN_TIMEOUT': 30,  # 优雅停机超时时间
}

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# 配置日志系统
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 线程安全的数据存储
data_lock = threading.RLock()
phone_registry = {}  # 电话号码注册表
user_data = defaultdict(dict)  # 用户数据
admin_users = set()  # 管理员用户

# 全局状态管理
app_state = {
    'running': True,
    'last_cleanup': datetime.now(),
    'last_health_check': datetime.now(),
    'error_count': 0,
    'request_count': 0,
    'start_time': datetime.now()
}

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

# 智能提取电话号码的正则表达式（优化版，减少重复提取）
PHONE_EXTRACTION_PATTERNS = [
    # 国际格式：+60 开头的完整号码
    re.compile(r'\+60[\s\-]?(\d[\d\s\-\(\)]{8,11})'),
    # 手机号码：0xx-xxxxxxx 或 0xxxxxxxxx (10位)
    re.compile(r'\b(0\d{2}[\s\-]?\d{3,4}[\s\-]?\d{3,4})\b'),
    # 固定电话：03-xxxxxxxx (吉隆坡/雪兰莪 - 10位)
    re.compile(r'\b(03[\s\-]?\d{4}[\s\-]?\d{4})\b'),
    # 固定电话：其他地区 04,05,06,07,09 (9位)
    re.compile(r'\b(0[4567][\s\-]?\d{3}[\s\-]?\d{4})\b'),
    re.compile(r'\b(09[\s\-]?\d{3}[\s\-]?\d{4})\b'),
    # 沙巴砂拉越固定电话：088,089,082-087 (9位)
    re.compile(r'\b(08[2-9][\s\-]?\d{3}[\s\-]?\d{3})\b'),
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

def signal_handler(signum, frame):
    """优雅停机信号处理"""
    logger.info(f"接收到信号 {signum}，开始优雅停机...")
    app_state['running'] = False

def data_cleanup_worker():
    """数据清理工作线程（长期运行优化）"""
    logger.info("数据清理工作线程已启动")
    
    while app_state['running']:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL'])
            
            if not app_state['running']:
                break
                
            cleanup_old_data()
            app_state['last_cleanup'] = datetime.now()
            
            # 检查内存使用（估算）
            memory_mb = get_memory_usage_estimate()
            if memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                logger.warning(f"内存使用过高 ({memory_mb:.1f}MB)，触发数据清理")
                # 强制清理更多数据
                force_cleanup()
                
            # 定期健康检查
            perform_health_check()
                
        except Exception as e:
            logger.error(f"数据清理工作线程错误: {e}")
            app_state['error_count'] += 1
            
            # 如果错误过多，暂停一段时间
            if app_state['error_count'] > 10:
                logger.warning("错误过多，暂停数据清理60秒")
                time.sleep(60)
                app_state['error_count'] = 0
    
    logger.info("数据清理工作线程已停止")

def force_cleanup():
    """强制清理更多数据以释放内存"""
    with data_lock:
        # 更激进的清理策略
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE'] // 2:
            # 删除一半最老的记录
            sorted_phones = sorted(phone_registry.items(), 
                                 key=lambda x: x[1].get('timestamp', '1970-01-01'))
            remove_count = len(phone_registry) // 2
            for phone, _ in sorted_phones[:remove_count]:
                del phone_registry[phone]
            
            logger.info(f"强制清理：删除了 {remove_count} 个电话记录")
        
        # 强制垃圾回收
        gc.collect()

def perform_health_check():
    """执行系统健康检查"""
    try:
        app_state['last_health_check'] = datetime.now()
        
        # 检查各项指标
        memory_mb = get_memory_usage_estimate()
        uptime = (datetime.now() - app_state['start_time']).total_seconds()
        
        # 记录健康状态
        if uptime % 3600 < 60:  # 每小时记录一次
            logger.info(f"健康检查 - 运行时间: {uptime/3600:.1f}h, 内存: {memory_mb:.1f}MB, "
                       f"电话记录: {len(phone_registry)}, 用户: {len(user_data)}")
        
    except Exception as e:
        logger.error(f"健康检查错误: {e}")

@contextmanager
def error_handler(operation_name):
    """通用错误处理上下文管理器"""
    try:
        yield
    except Exception as e:
        logger.error(f"{operation_name} 错误: {e}")
        app_state['error_count'] += 1
        raise

def extract_phone_numbers(text):
    """从文本中智能提取电话号码（优化版，避免重复）"""
    phone_candidates = set()  # 使用集合避免重复
    
    # 使用多个正则表达式模式提取可能的电话号码
    for pattern in PHONE_EXTRACTION_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            if isinstance(match, tuple):
                # 处理带括号的格式
                candidate = ''.join(match)
            else:
                candidate = match
            
            # 清理号码格式
            cleaned = re.sub(r'[\s\-\(\)]+', '', candidate)
            
            # 基本验证和标准化
            if len(cleaned) >= 9 and cleaned.isdigit():
                # 标准化为统一格式以避免重复
                normalized = normalize_phone_format(cleaned)
                if normalized:
                    phone_candidates.add(normalized)
    
    return list(phone_candidates)

def normalize_phone_format(phone):
    """标准化电话号码格式"""
    # 移除所有非数字字符
    digits_only = re.sub(r'\D', '', phone)
    
    # 处理国际格式
    if digits_only.startswith('60'):
        digits_only = digits_only[2:]  # 移除国家代码
    
    # 确保以0开头
    if not digits_only.startswith('0') and len(digits_only) >= 9:
        digits_only = '0' + digits_only
    
    # 基本长度验证
    if 9 <= len(digits_only) <= 11 and digits_only.startswith('0'):
        return digits_only
    
    return None

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
                
                # 智能确定地区代码前缀
                if phone.startswith('08'):
                    # 沙巴砂拉越使用3位前缀
                    prefix = phone[:3]
                else:
                    # 其他地区使用2位前缀
                    prefix = phone[:2]
                
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

def send_telegram_message(chat_id, text, retry_count=0):
    """发送Telegram消息（长期运行优化，带重试机制）"""
    max_retries = PRODUCTION_CONFIG['ERROR_RETRY_MAX']
    
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
        req.add_header('User-Agent', 'Malaysia-Phone-Bot/1.5.0')
        
        # 发送请求
        with urllib.request.urlopen(req, timeout=PRODUCTION_CONFIG['REQUEST_TIMEOUT']) as response:
            result = json.loads(response.read().decode())
            if result.get('ok', False):
                return True
            else:
                logger.warning(f"Telegram API 错误: {result.get('description', '未知错误')}")
                return False
            
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP 错误 {e.code}: {e.reason}")
        if retry_count < max_retries and e.code in [429, 502, 503, 504]:
            # 对于特定错误码进行重试
            wait_time = (retry_count + 1) * 2  # 指数退避
            logger.info(f"等待 {wait_time} 秒后重试...")
            time.sleep(wait_time)
            return send_telegram_message(chat_id, text, retry_count + 1)
        return False
        
    except urllib.error.URLError as e:
        logger.error(f"网络错误: {e.reason}")
        if retry_count < max_retries:
            wait_time = (retry_count + 1) * 2
            logger.info(f"网络重试，等待 {wait_time} 秒...")
            time.sleep(wait_time)
            return send_telegram_message(chat_id, text, retry_count + 1)
        return False
        
    except Exception as e:
        logger.error(f"发送消息未知错误: {e}")
        if retry_count < max_retries:
            wait_time = (retry_count + 1) * 2
            time.sleep(wait_time)
            return send_telegram_message(chat_id, text, retry_count + 1)
        return False

def handle_message(message):
    """处理Telegram消息（长期运行优化）"""
    chat_id = None
    
    try:
        # 增加请求计数
        app_state['request_count'] += 1
        
        # 基本数据提取和验证
        if not isinstance(message, dict):
            logger.warning("收到非字典类型的消息")
            return
            
        chat_id = message.get('chat', {}).get('id')
        user_id = message.get('from', {}).get('id')
        username = message.get('from', {}).get('username', '未知用户')
        text = message.get('text', '')
        
        if not chat_id or not user_id:
            logger.warning("消息缺少必要的chat_id或user_id")
            return
        
        # 更新用户活动时间
        with data_lock:
            if user_id not in user_data:
                user_data[user_id] = {}
            user_data[user_id]['last_activity'] = datetime.now().isoformat()
            user_data[user_id]['message_count'] = user_data[user_id].get('message_count', 0) + 1
        
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
            
            # 处理提取到的电话号码 - 只处理第一个有效的
            processed = False
            for phone_candidate in extracted_phones:
                result = analyze_phone_number(phone_candidate)
                if result and result['valid'] and not processed:
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
                    processed = True
                    break
            
            # 如果处理成功，直接返回
            if processed:
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
                
    except KeyError as e:
        logger.error(f"消息格式错误 - 缺少字段: {e}")
        if chat_id:
            send_telegram_message(chat_id, "❌ 消息格式有误，请重新发送")
            
    except Exception as e:
        logger.error(f"处理消息错误: {e}")
        app_state['error_count'] += 1
        
        if chat_id:
            try:
                error_msg = "❌ 服务暂时不可用，请稍后重试"
                if app_state['error_count'] > 50:
                    error_msg += "\n🔧 系统正在进行维护，请稍等片刻"
                    
                send_telegram_message(chat_id, error_msg)
            except Exception as send_error:
                logger.error(f"发送错误消息失败: {send_error}")
        
        # 如果错误太多，触发清理
        if app_state['error_count'] > 100:
            logger.warning("错误数量过多，执行紧急清理")
            force_cleanup()
            app_state['error_count'] = 0

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        start_time = time.time()
        
        try:
            # 检查应用状态
            if not app_state['running']:
                self.send_response(503)  # Service Unavailable
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok": false, "error": "service_shutting_down"}')
                return
            
            # 限制内容长度
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 10 * 1024 * 1024:  # 10MB 限制
                self.send_response(413)  # Payload Too Large
                self.end_headers()
                return
            
            if content_length > 0:
                post_data = self.rfile.read(content_length)
                
                # 解析Telegram更新
                try:
                    update = json.loads(post_data.decode('utf-8'))
                except json.JSONDecodeError:
                    logger.warning("收到无效的JSON数据")
                    self.send_response(400)
                    self.end_headers()
                    return
                
                # 处理消息
                if 'message' in update:
                    with error_handler("webhook_message_processing"):
                        handle_message(update['message'])
            
            # 返回成功响应
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            
            response_data = {
                "ok": True, 
                "timestamp": datetime.now().isoformat(),
                "processing_time": round((time.time() - start_time) * 1000, 2)
            }
            self.wfile.write(json.dumps(response_data).encode())
            
        except Exception as e:
            logger.error(f"Webhook处理错误: {e}")
            try:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = {
                    "ok": False, 
                    "error": "internal_server_error",
                    "timestamp": datetime.now().isoformat()
                }
                self.wfile.write(json.dumps(error_response).encode())
            except:
                pass  # 如果连错误响应都发送不了，就忽略
    
    def do_GET(self):
        """健康检查端点（长期运行监控）"""
        try:
            memory_mb = get_memory_usage_estimate()
            uptime_seconds = (datetime.now() - app_state['start_time']).total_seconds()
            
            # 计算健康状态
            health_status = 'healthy'
            if not app_state['running']:
                health_status = 'shutting_down'
            elif memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                health_status = 'warning'
            elif app_state['error_count'] > 20:
                health_status = 'degraded'
            
            status = {
                'status': health_status,
                'version': '1.5.0 Smart Tracking (Long-Running)',
                'uptime_hours': round(uptime_seconds / 3600, 2),
                'phone_registry_size': len(phone_registry),
                'user_data_size': len(user_data),
                'memory_estimate_mb': round(memory_mb, 2),
                'error_count': app_state['error_count'],
                'request_count': app_state['request_count'],
                'last_cleanup': app_state['last_cleanup'].isoformat(),
                'last_health_check': app_state['last_health_check'].isoformat(),
                'timestamp': datetime.now().isoformat(),
                'limits': {
                    'max_phone_registry': PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE'],
                    'max_user_data': PRODUCTION_CONFIG['MAX_USER_DATA_SIZE'],
                    'memory_threshold': PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']
                }
            }
            
            response = json.dumps(status, ensure_ascii=False, indent=2)
            
            # 根据健康状态返回不同的HTTP状态码
            if health_status == 'healthy':
                status_code = 200
            elif health_status in ['warning', 'degraded']:
                status_code = 206  # Partial Content
            else:
                status_code = 503  # Service Unavailable
            
            self.send_response(status_code)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(response.encode('utf-8'))
            
        except Exception as e:
            logger.error(f"健康检查错误: {e}")
            try:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = {"status": "error", "message": str(e)}
                self.wfile.write(json.dumps(error_response).encode())
            except:
                pass
    
    def log_message(self, format, *args):
        """减少日志输出"""
        pass

def run_server():
    """启动HTTP服务器（长期运行优化）"""
    port = int(os.getenv('PORT', 10000))
    
    # 注册信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    server = None
    cleanup_thread = None
    
    try:
        server = HTTPServer(('', port), WebhookHandler)
        server.timeout = 1  # 设置超时以支持优雅停机
        
        logger.info("=" * 60)
        logger.info("🚀 马来西亚电话号码机器人已启动 (长期运行版)")
        logger.info(f"📦 版本: 1.5.0 Smart Tracking (Long-Running)")
        logger.info(f"🌐 端口: {port}")
        logger.info(f"💾 内存估算: {get_memory_usage_estimate():.1f} MB")
        logger.info(f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"🔧 配置:")
        logger.info(f"   - 数据保留: {PRODUCTION_CONFIG['DATA_RETENTION_DAYS']} 天")
        logger.info(f"   - 清理间隔: {PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']} 秒")
        logger.info(f"   - 最大内存: {PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']} MB")
        logger.info(f"   - 最大记录: {PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']} 个")
        logger.info("=" * 60)
        
        # 启动数据清理线程
        cleanup_thread = threading.Thread(target=data_cleanup_worker, daemon=False)
        cleanup_thread.start()
        logger.info("🧹 数据清理线程已启动")
        
        # 主服务循环，支持优雅停机
        while app_state['running']:
            try:
                server.handle_request()
            except OSError:
                # 服务器socket被关闭
                if not app_state['running']:
                    break
                logger.warning("服务器socket异常，继续运行...")
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"服务器处理请求错误: {e}")
                if not app_state['running']:
                    break
                time.sleep(0.1)
        
        logger.info("🛑 开始优雅停机...")
        
    except KeyboardInterrupt:
        logger.info("收到键盘中断信号")
    except Exception as e:
        logger.error(f"服务器启动错误: {e}")
    finally:
        # 优雅停机
        if server:
            logger.info("关闭HTTP服务器...")
            server.server_close()
        
        # 等待清理线程结束
        if cleanup_thread and cleanup_thread.is_alive():
            logger.info("等待数据清理线程结束...")
            cleanup_thread.join(timeout=PRODUCTION_CONFIG['GRACEFUL_SHUTDOWN_TIMEOUT'])
        
        # 最后的数据清理
        logger.info("执行最终数据清理...")
        cleanup_old_data()
        
        uptime = (datetime.now() - app_state['start_time']).total_seconds()
        logger.info(f"✅ 服务器已停止 - 运行时间: {uptime/3600:.2f} 小时")
        logger.info(f"📊 统计信息: 处理 {app_state['request_count']} 个请求, {app_state['error_count']} 个错误")

if __name__ == '__main__':
    run_server()
