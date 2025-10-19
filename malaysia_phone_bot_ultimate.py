#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码机器人 - 智能追踪版本 (零依赖版本)
专为Render等云平台设计，零依赖，智能提取+重复追踪
完整记录号码出现历史和用户统计
 
作者: MiniMax Agent
版本: 1.8.0 Persistent Storage (Data Preservation)
更新时间: 2025-10-13 (v1.8.0 Enhanced Data Persistence)
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
import shutil
from contextlib import contextmanager

# 生产环境配置（长期运行优化）
PRODUCTION_CONFIG = {
    'MAX_PHONE_REGISTRY_SIZE': 50000,  # 最大电话号码记录数（增加到5万）
    'MAX_USER_DATA_SIZE': 10000,      # 最大用户数据记录数（增加到1万）
    'DATA_CLEANUP_INTERVAL': 3600,    # 数据清理间隔（1小时）
    'DATA_RETENTION_DAYS': 45,        # 数据保留天数（一个半月）
    'AUTO_RESTART_MEMORY_MB': 800,    # 内存使用超过此值时自动重启
    'MAX_MESSAGE_LENGTH': 4096,       # Telegram消息最大长度
    'REQUEST_TIMEOUT': 15,            # HTTP请求超时时间
    'MAX_CONCURRENT_REQUESTS': 10,    # 最大并发请求数
    'HEALTH_CHECK_INTERVAL': 300,     # 健康检查间隔（5分钟）
    'ERROR_RETRY_MAX': 3,             # 最大重试次数
    'GRACEFUL_SHUTDOWN_TIMEOUT': 30,  # 优雅停机超时时间
    'DATA_SAVE_INTERVAL': 600,        # 数据保存间隔（10分钟）
    'BACKUP_RETENTION_DAYS': 90,      # 备份文件保留天数（3个月）
}

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# 数据持久化文件路径
DATA_DIR = 'data'
PHONE_REGISTRY_FILE = os.path.join(DATA_DIR, 'phone_registry.json')
USER_DATA_FILE = os.path.join(DATA_DIR, 'user_data.json')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

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
    'start_time': datetime.now(),
    'auto_restart_enabled': True
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

# 智能提取电话号码的正则表达式
PHONE_EXTRACTION_PATTERNS = [
    # 马来西亚国际格式
    re.compile(r'\+60[\s\-]?(\d[\d\s\-\(\)]{8,11})'),
    
    # 标准固定电话格式
    re.compile(r'\b(0\d{2}[\s\-]?\d{3,4}[\s\-]?\d{3,4})\b'),
    
    # 特定地区格式
    re.compile(r'\b(03[\s\-]?\d{4}[\s\-]?\d{4})\b'),
    re.compile(r'\b(0[4567][\s\-]?\d{3}[\s\-]?\d{4})\b'),
    re.compile(r'\b(09[\s\-]?\d{3}[\s\-]?\d{4})\b'),
    re.compile(r'\b(08[2-9][\s\-]?\d{3}[\s\-]?\d{3})\b'),
    
    # 带括号格式
    re.compile(r'\(?(0\d{2,3})\)?[\s\-]?(\d{3,4})[\s\-]?(\d{3,4})'),
    
    # 增强的灵活格式
    re.compile(r'\b(\d{2,3}[\s\-]\d{3,4}[\s\-]\d{3,4})\b'),  # 123-456-789
    re.compile(r'\b(\d{2}\s+\d{4}\s+\d{3})\b'),              # 12 3456 789
    re.compile(r'\b(\d{3}\s+\d{3}\s+\d{3,4})\b'),            # 123 456 789
    
    # 纯数字格式（9-11位）
    re.compile(r'\b(\d{9,11})\b'),
    
    # 修正模式（不带边界）
    re.compile(r'(\d{2}\s+\d{4}\s+\d{3})'),                  # 12 3456 789
    re.compile(r'(0\d-\d{4}-\d{4})'),                        # 03-1234-5678
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

def get_memory_usage_estimate():
    """估算内存使用情况（基于数据结构大小）"""
    try:
        phone_count = len(phone_registry)
        user_count = len(user_data)
        estimated_mb = 50 + (phone_count + user_count) * 0.001
        return estimated_mb
    except:
        return 0

def ensure_data_directories():
    """确保数据目录存在"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        logger.info(f"数据目录已创建: {DATA_DIR}")
    except Exception as e:
        logger.error(f"创建数据目录失败: {e}")

def save_data_to_file():
    """保存数据到文件"""
    try:
        with data_lock:
            # 保存电话号码注册表
            with open(PHONE_REGISTRY_FILE, 'w', encoding='utf-8') as f:
                json.dump(phone_registry, f, ensure_ascii=False, indent=2)
            
            # 保存用户数据
            user_data_dict = dict(user_data)  # 转换 defaultdict 为普通字典
            with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(user_data_dict, f, ensure_ascii=False, indent=2)
            
            logger.info(f"数据已保存 - 电话记录: {len(phone_registry)}, 用户数据: {len(user_data)}")
            return True
    except Exception as e:
        logger.error(f"保存数据失败: {e}")
        return False

def load_data_from_file():
    """从文件加载数据"""
    try:
        global phone_registry, user_data
        
        # 加载电话号码注册表
        if os.path.exists(PHONE_REGISTRY_FILE):
            try:
                with open(PHONE_REGISTRY_FILE, 'r', encoding='utf-8') as f:
                    loaded_phone_registry = json.load(f)
                    if isinstance(loaded_phone_registry, dict):
                        with data_lock:
                            phone_registry.update(loaded_phone_registry)
                        logger.info(f"已加载电话记录: {len(phone_registry)} 个")
                    else:
                        logger.warning("电话注册表文件格式错误，已忽略")
            except json.JSONDecodeError as e:
                logger.error(f"电话注册表文件JSON格式错误: {e}")
                # 备份损坏的文件
                backup_corrupted_file = f"{PHONE_REGISTRY_FILE}.corrupted.{int(time.time())}"
                shutil.move(PHONE_REGISTRY_FILE, backup_corrupted_file)
                logger.info(f"已将损坏文件移动到: {backup_corrupted_file}")
        else:
            logger.info("电话注册表文件不存在，从空数据开始")
        
        # 加载用户数据
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                    loaded_user_data = json.load(f)
                    if isinstance(loaded_user_data, dict):
                        with data_lock:
                            for user_id, data in loaded_user_data.items():
                                try:
                                    user_data[int(user_id)] = data
                                except (ValueError, TypeError):
                                    logger.warning(f"跳过无效用户ID: {user_id}")
                        logger.info(f"已加载用户数据: {len(user_data)} 个")
                    else:
                        logger.warning("用户数据文件格式错误，已忽略")
            except json.JSONDecodeError as e:
                logger.error(f"用户数据文件JSON格式错误: {e}")
                # 备份损坏的文件
                backup_corrupted_file = f"{USER_DATA_FILE}.corrupted.{int(time.time())}"
                shutil.move(USER_DATA_FILE, backup_corrupted_file)
                logger.info(f"已将损坏文件移动到: {backup_corrupted_file}")
        else:
            logger.info("用户数据文件不存在，从空数据开始")
        
        return True
    except Exception as e:
        logger.error(f"加载数据失败: {e}")
        return False

def create_backup():
    """创建数据备份"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_phone_file = os.path.join(BACKUP_DIR, f'phone_registry_{timestamp}.json')
        backup_user_file = os.path.join(BACKUP_DIR, f'user_data_{timestamp}.json')
        
        backup_created = False
        
        # 复制当前数据文件到备份目录
        if os.path.exists(PHONE_REGISTRY_FILE):
            shutil.copy2(PHONE_REGISTRY_FILE, backup_phone_file)
            backup_created = True
        else:
            logger.debug("电话注册表文件不存在，跳过备份")
        
        if os.path.exists(USER_DATA_FILE):
            shutil.copy2(USER_DATA_FILE, backup_user_file)
            backup_created = True
        else:
            logger.debug("用户数据文件不存在，跳过备份")
        
        if backup_created:
            logger.info(f"数据备份已创建: {timestamp}")
        else:
            logger.debug("没有数据文件需要备份")
        
        return True
    except Exception as e:
        logger.error(f"创建备份失败: {e}")
        return False

def cleanup_old_backups():
    """清理过期的备份文件"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return
        
        cutoff_time = datetime.now() - timedelta(days=PRODUCTION_CONFIG['BACKUP_RETENTION_DAYS'])
        deleted_count = 0
        
        for filename in os.listdir(BACKUP_DIR):
            file_path = os.path.join(BACKUP_DIR, filename)
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if file_time < cutoff_time:
                    os.remove(file_path)
                    deleted_count += 1
        
        if deleted_count > 0:
            logger.info(f"已清理 {deleted_count} 个过期备份文件")
    except Exception as e:
        logger.error(f"清理备份文件失败: {e}")

def cleanup_old_data():
    """清理过期数据（保守策略，延长保留期）"""
    with data_lock:
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(days=PRODUCTION_CONFIG['DATA_RETENTION_DAYS'])
        
        initial_phone_count = len(phone_registry)
        initial_user_count = len(user_data)
        
        # 清理过期的电话号码记录（只有在数量过多时才清理）
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE'] * 0.8:
            expired_phones = []
            for phone, data in phone_registry.items():
                try:
                    timestamp_str = data.get('timestamp', '1970-01-01')
                    if 'T' not in timestamp_str:
                        timestamp_str += 'T00:00:00'
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    if timestamp < cutoff_time:
                        expired_phones.append(phone)
                except:
                    # 如果时间解析失败，保留数据
                    continue
            
            for phone in expired_phones:
                del phone_registry[phone]
        
        # 清理过期的用户数据（同样保守策略）
        if len(user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE'] * 0.8:
            expired_users = []
            for user_id, data in user_data.items():
                try:
                    activity_str = data.get('last_activity', '1970-01-01')
                    if 'T' not in activity_str:
                        activity_str += 'T00:00:00'
                    activity_time = datetime.fromisoformat(activity_str.replace('Z', '+00:00'))
                    if activity_time < cutoff_time:
                        expired_users.append(user_id)
                except:
                    # 如果时间解析失败，保留数据
                    continue
            
            for user_id in expired_users:
                del user_data[user_id]
        
        # 只有在达到绝对上限时才强制清理
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:
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
        
        # 在清理后保存数据
        save_data_to_file()
        
        gc.collect()
        cleaned_phones = initial_phone_count - len(phone_registry)
        cleaned_users = initial_user_count - len(user_data)
        logger.info(f"数据清理完成 - 清理电话记录: {cleaned_phones}, 清理用户数据: {cleaned_users}")
        logger.info(f"当前数据 - 电话记录: {len(phone_registry)}, 用户数据: {len(user_data)}")

def signal_handler(signum, frame):
    """优雅停机信号处理"""
    logger.info(f"接收到信号 {signum}，开始优雅停机...")
    app_state['running'] = False
    
    # 在收到停机信号时，如果启用了自动重启，立即重启
    if app_state['auto_restart_enabled'] and signum == signal.SIGTERM:
        logger.info("🔄 检测到Render平台重启信号，准备自动重启...")
        restart_application()

def restart_application():
    """重启应用程序"""
    try:
        logger.info("🔄 正在重启应用程序...")
        os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        logger.error(f"重启失败: {e}")
        sys.exit(1)

def data_cleanup_worker():
    """数据清理工作线程"""
    logger.info("🧹 数据清理线程已启动")
    
    while app_state['running']:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL'])
            
            if not app_state['running']:
                break
                
            cleanup_old_data()
            app_state['last_cleanup'] = datetime.now()
            
            # 检查内存使用
            memory_mb = get_memory_usage_estimate()
            if memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                logger.warning(f"内存使用过高 ({memory_mb:.1f}MB)，触发数据清理")
                force_cleanup()
                
            perform_health_check()
                
        except Exception as e:
            logger.error(f"数据清理工作线程错误: {e}")
            app_state['error_count'] += 1
            
            if app_state['error_count'] > 10:
                logger.warning("错误过多，暂停数据清理60秒")
                time.sleep(60)
                app_state['error_count'] = 0
    
    logger.info("数据清理工作线程已停止")

def data_save_worker():
    """数据保存工作线程"""
    logger.info("💾 数据保存线程已启动")
    last_backup_hour = -1
    
    while app_state['running']:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_SAVE_INTERVAL'])
            
            if not app_state['running']:
                break
            
            # 定期保存数据
            save_success = save_data_to_file()
            if save_success:
                logger.debug("定期数据保存成功")
            
            # 每小时创建一次备份（避免重复备份）
            current_time = datetime.now()
            current_hour = current_time.hour
            
            if current_hour != last_backup_hour and current_time.minute < 30:  # 每小时的前30分钟内
                backup_success = create_backup()
                if backup_success:
                    cleanup_old_backups()
                    last_backup_hour = current_hour
                    logger.debug(f"小时备份完成: {current_hour}:00")
                
        except Exception as e:
            logger.error(f"数据保存工作线程错误: {e}")
            time.sleep(60)  # 错误时等待1分钟再继续
    
    # 线程结束前最后保存一次数据
    logger.info("数据保存线程即将停止，执行最终保存...")
    try:
        save_data_to_file()
        create_backup()
        logger.info("最终数据保存完成")
    except Exception as e:
        logger.error(f"最终数据保存失败: {e}")
    
    logger.info("数据保存工作线程已停止")

def force_cleanup():
    """强制清理更多数据以释放内存"""
    with data_lock:
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE'] // 2:
            sorted_phones = sorted(phone_registry.items(), 
                                 key=lambda x: x[1].get('timestamp', '1970-01-01'))
            remove_count = len(phone_registry) // 2
            for phone, _ in sorted_phones[:remove_count]:
                del phone_registry[phone]
            
            logger.info(f"强制清理：删除了 {remove_count} 个电话记录")
        
        gc.collect()

def perform_health_check():
    """执行系统健康检查"""
    try:
        app_state['last_health_check'] = datetime.now()
        
        memory_mb = get_memory_usage_estimate()
        uptime = (datetime.now() - app_state['start_time']).total_seconds()
        
        if uptime % 3600 < 60:  # 每小时记录一次
            logger.info(f"健康检查 - 运行时间: {uptime/3600:.1f}h, 内存: {memory_mb:.1f}MB, "
                       f"电话记录: {len(phone_registry)}, 用户: {len(user_data)}")
        
        # 发送心跳信号到Render（防止服务被停止）
        send_heartbeat()
        
    except Exception as e:
        logger.error(f"健康检查错误: {e}")

def send_heartbeat():
    """发送心跳信号到Render"""
    try:
        # 向自己的健康检查端点发送请求，保持活跃
        webhook_url = os.getenv('WEBHOOK_URL') or f"https://telegram-phone-bot-ouq9.onrender.com"
        health_url = f"{webhook_url}/health"
        
        req = urllib.request.Request(health_url, method='GET')
        req.add_header('User-Agent', 'Bot-Heartbeat/1.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                logger.debug("心跳信号发送成功")
            
    except Exception as e:
        logger.debug(f"心跳信号发送失败: {e}")

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
    """从文本中智能提取电话号码"""
    phone_candidates = set()
    
    for pattern in PHONE_EXTRACTION_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            if isinstance(match, tuple):
                candidate = ''.join(match)
            else:
                candidate = match
            
            cleaned = re.sub(r'[\s\-\(\)]+', '', candidate)
            
            if len(cleaned) >= 9 and cleaned.isdigit():
                normalized = normalize_phone_format(cleaned)
                if normalized:
                    phone_candidates.add(normalized)
    
    return list(phone_candidates)

def normalize_phone_format(phone):
    """增强的电话号码标准化格式"""
    # 移除所有非数字字符
    digits_only = re.sub(r'\D', '', phone)
    
    # 处理马来西亚国际代码
    if digits_only.startswith('60'):
        digits_only = digits_only[2:]
    
    # 验证长度
    if len(digits_only) < 9 or len(digits_only) > 11:
        return None
    
    # 添加0前缀（如果没有）
    if not digits_only.startswith('0'):
        digits_only = '0' + digits_only
    
    # 最终验证
    if len(digits_only) < 10 or len(digits_only) > 11:
        return None
    
    return digits_only

@lru_cache(maxsize=1000)
def analyze_phone_number(normalized_phone):
    """分析电话号码"""
    if len(normalized_phone) < 9:
        return {
            'carrier': '无效号码',
            'location': '格式错误',
            'type': 'invalid',
            'formatted': normalized_phone
        }
    
    # 检查3位前缀（沙巴砂拉越）
    for prefix in ['082', '083', '084', '085', '086', '087', '088', '089']:
        if normalized_phone.startswith(prefix):
            return {
                'carrier': '固话',
                'location': STATE_MAPPING.get(prefix, '未知地区'),
                'type': 'landline',
                'formatted': f"{prefix}-{normalized_phone[3:6]}-{normalized_phone[6:]}"
            }
    
    # 检查手机号码前缀
    mobile_prefix = normalized_phone[:3]
    if mobile_prefix in OPERATOR_MAPPING:
        return {
            'carrier': OPERATOR_MAPPING[mobile_prefix],
            'location': MOBILE_COVERAGE_MAPPING.get(OPERATOR_MAPPING[mobile_prefix], '马来西亚'),
            'type': 'mobile',
            'formatted': f"{mobile_prefix}-{normalized_phone[3:6]}-{normalized_phone[6:]}"
        }
    
    # 检查2位固话前缀
    landline_prefix = normalized_phone[:2]
    if landline_prefix in STATE_MAPPING:
        return {
            'carrier': '固话',
            'location': STATE_MAPPING[landline_prefix],
            'type': 'landline',
            'formatted': f"{landline_prefix}-{normalized_phone[2:6]}-{normalized_phone[6:]}"
        }
    
    return {
        'carrier': '未知',
        'location': '未知地区',
        'type': 'unknown',
        'formatted': normalized_phone
    }

def get_user_display_name(user_id, user_info=None):
    """获取用户显示名称"""
    try:
        with data_lock:
            # 先从 user_data 中获取已存储的用户信息
            if user_id in user_data:
                stored_data = user_data[user_id]
                first_name = stored_data.get('first_name', '')
                last_name = stored_data.get('last_name', '')
                username = stored_data.get('username', '')
                
                if first_name or last_name:
                    return f"{first_name} {last_name}".strip()
                elif username:
                    return f"@{username}"
            
            # 如果传入了当前用户信息，使用当前信息
            if user_info:
                first_name = user_info.get('first_name', '')
                last_name = user_info.get('last_name', '')
                username = user_info.get('username', '')
                
                if first_name or last_name:
                    return f"{first_name} {last_name}".strip()
                elif username:
                    return f"@{username}"
            
            # 从 phone_registry中查找已存储的名称
            for phone_data in phone_registry.values():
                if phone_data.get('user_id') == user_id:
                    stored_name = phone_data.get('first_user_name')
                    if stored_name:
                        return stored_name
                    
                    # 尝试从存储的用户数据中构建名称
                    first_name = phone_data.get('first_name', '')
                    last_name = phone_data.get('last_name', '')
                    username = phone_data.get('username', '')
                    
                    if first_name or last_name:
                        return f"{first_name} {last_name}".strip()
                    elif username:
                        return f"@{username}"
            
            # 如果都没有，返回默认名称
            return f"用户{user_id}"
            
    except Exception as e:
        logger.error(f"获取用户显示名称错误: {e}")
        return f"用户{user_id}"

def get_simple_user_display_name(user_info):
    """简化的用户显示名称函数（用于直接传入用户信息字典）"""
    try:
        if not isinstance(user_info, dict):
            return f"用户{user_info}"
        
        first_name = user_info.get('first_name', '').strip()
        last_name = user_info.get('last_name', '').strip()
        username = user_info.get('username', '').strip()
        user_id = user_info.get('id', '')
        
        # 优先使用全名
        if first_name or last_name:
            full_name = f"{first_name} {last_name}".strip()
            return full_name
        
        # 其次使用用户名
        if username:
            return f"@{username}"
        
        # 最后使用用户ID
        return f"用户{user_id}"
        
    except Exception as e:
        logger.error(f"获取简化用户显示名称错误: {e}")
        return f"用户{user_info.get('id', 'Unknown') if isinstance(user_info, dict) else user_info}"

def send_telegram_message(chat_id, text, reply_to_message_id=None):
    """发送Telegram消息（带重试机制）"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    payload = {
        'chat_id': chat_id,
        'text': text[:PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH']],
        'parse_mode': 'HTML'
    }
    
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    # 重试机制
    for attempt in range(PRODUCTION_CONFIG['ERROR_RETRY_MAX']):
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data)
            req.add_header('Content-Type', 'application/json')
            
            with urllib.request.urlopen(req, timeout=PRODUCTION_CONFIG['REQUEST_TIMEOUT']) as response:
                if response.status == 200:
                    return True
                    
        except Exception as e:
            logger.warning(f"发送消息失败 (尝试 {attempt + 1}/{PRODUCTION_CONFIG['ERROR_RETRY_MAX']}): {e}")
            if attempt < PRODUCTION_CONFIG['ERROR_RETRY_MAX'] - 1:
                time.sleep(2 ** attempt)
    
    return False

def handle_text(message_data):
    """处理文本消息"""
    try:
        with error_handler("消息处理"):
            chat_id = message_data['chat']['id']
            user_id = message_data['from']['id']
            text = message_data.get('text', '')
            message_id = message_data.get('message_id')
            
            # 更新用户活动时间和信息
            with data_lock:
                user_data[user_id]['last_activity'] = datetime.now().isoformat()
                user_data[user_id]['username'] = message_data['from'].get('username', '')
                user_data[user_id]['first_name'] = message_data['from'].get('first_name', '')
                user_data[user_id]['last_name'] = message_data['from'].get('last_name', '')
            
            # 处理命令
            if text.startswith('/'):
                handle_command(chat_id, user_id, text, message_id)
                return
            
            # 提取电话号码
            phone_numbers = extract_phone_numbers(text)
            
            if not phone_numbers:
                send_telegram_message(
                    chat_id,
                    "⚠️ 未检测到有效的马来西亚电话号码\n\n"
                    "请发送包含电话号码的消息，支持格式：\n"
                    "• +60 12-345 6789\n"
                    "• 012-345 6789\n"
                    "• 0123456789\n"
                    "• 03-1234 5678（固话）",
                    message_id
                )
                return
            
            # 分析和注册电话号码
            response_parts = ["📞 <b>查号引导人</b>\n"]
            duplicates_found = False
            
            for phone in phone_numbers:
                analysis = analyze_phone_number(phone)
                
                # 注册号码并检查重复
                with data_lock:
                    if phone in phone_registry:
                        phone_registry[phone]['count'] += 1
                        phone_registry[phone]['last_seen'] = datetime.now().isoformat()
                        duplicates_found = True
                        
                        # 获取首次记录用户信息
                        first_user_id = phone_registry[phone].get('user_id')
                        first_user_name = get_user_display_name(first_user_id) if first_user_id else "未知用户"
                        # 格式化时间显示
                        timestamp_str = phone_registry[phone]['timestamp']
                        try:
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            first_time = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            first_time = timestamp_str[:19]  # 备用格式
                        
                        # 获取当前用户名称
                        current_user_name = get_user_display_name(user_id, message_data['from'])
                        
                        # 判断是否是同一用户
                        if first_user_id == user_id:
                            duplicate_info = f"🔄 <b>您曾经记录过此号码</b>"
                        else:
                            duplicate_info = f"⚠️ <b>重复提醒</b>\n   📞 此号码已被用户 <b>{first_user_name}</b> 使用"
                        
                        response_parts.append(
                            f"📞 <b>号码引导</b>\n"
                            f"🔢 当前号码: {analysis['formatted']}\n"
                            f"🇲🇾 号码归属地: {analysis['location']}\n"
                            f"📱 首次记录时间: {first_time}\n"
                            f"🔁 历史交互: {phone_registry[phone]['count']}次\n"
                            f"👥 涉及用户: 1人\n\n"
                            f"{duplicate_info}\n"
                        )
                    else:
                        # 获取当前用户显示名称
                        current_user_name = get_user_display_name(user_id, message_data['from'])
                        
                        phone_registry[phone] = {
                            'timestamp': datetime.now().isoformat(),
                            'count': 1,
                            'last_seen': datetime.now().isoformat(),
                            'user_id': user_id,
                            'chat_id': chat_id,
                            'first_user_name': current_user_name,
                            'username': message_data['from'].get('username', ''),
                            'first_name': message_data['from'].get('first_name', ''),
                            'last_name': message_data['from'].get('last_name', '')
                        }
                        
                        response_parts.append(
                            f"📞 <b>号码引导</b>\n"
                            f"🔢 当前号码: {analysis['formatted']}\n"
                            f"🇲🇾 号码归属地: {analysis['location']}\n"
                            f"📱 首次记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"🔁 历史交互: 1次\n"
                            f"👥 涉及用户: 1人\n\n"
                            f"✅ <b>新号码记录</b>\n"
                            f"   👤 记录者: {current_user_name}\n"
                        )
            
            # 移除底部统计信息，保持显示简洁
            
            response_text = '\n'.join(response_parts)
            send_telegram_message(chat_id, response_text, message_id)
            
    except Exception as e:
        logger.error(f"处理文本消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理消息时发生错误，请稍后重试")

def handle_command(chat_id, user_id, command, message_id=None):
    """处理命令"""
    try:
        if command == '/start':
            welcome_text = (
                "🇲🇾 <b>马来西亚电话号码智能追踪机器人</b>\n\n"
                "✨ <b>功能特色</b>:\n"
                "📱 智能识别手机/固话号码\n"
                "🎯 精确归属地/运营商查询\n"
                "🔄 重复号码追踪统计\n"
                "📊 完整的使用数据分析\n\n"
                "📝 <b>使用方法</b>:\n"
                "直接发送包含电话号码的消息即可\n\n"
                "🤖 <b>命令列表</b>:\n"
                "/help - 帮助信息\n"
                "/stats - 查看统计\n"
                "/duplicates - 查看重复号码\n"
                "/save - 手动保存数据\n"
                "/clear - 清理数据（管理员）\n\n"
                f"🚀 <b>版本</b>: 1.8.0 Persistent Storage\n"
                f"⏰ <b>启动时间</b>: {app_state['start_time'].strftime('%Y-%m-%d %H:%M:%S')}"
            )
            send_telegram_message(chat_id, welcome_text, message_id)
            
        elif command == '/help':
            help_text = (
                "📖 <b>马来西亚电话号码机器人帮助</b>\n\n"
                "🎯 <b>支持的号码格式</b>:\n"
                "• +60 12-345 6789\n"
                "• 012-345 6789\n"
                "• 0123456789\n"
                "• 03-1234 5678（固话）\n"
                "• (03) 1234-5678\n\n"
                "📱 <b>识别信息</b>:\n"
                "• 运营商（Maxis/DiGi/Celcom/U Mobile）\n"
                "• 归属地（州属/地区）\n"
                "• 号码类型（手机/固话）\n"
                "• 重复记录统计\n\n"
                "🤖 <b>命令说明</b>:\n"
                "/start - 欢迎信息\n"
                "/help - 此帮助\n"
                "/stats - 统计信息\n"
                "/duplicates - 查看重复号码详情\n"
                "/save - 手动保存数据到文件\n"
                "/clear - 清理数据（仅管理员）\n\n"
                "💡 <b>提示</b>: 直接发送包含号码的文本即可分析"
            )
            send_telegram_message(chat_id, help_text, message_id)
            
        elif command == '/stats':
            with data_lock:
                total_phones = len(phone_registry)
                total_queries = sum(data.get('count', 0) for data in phone_registry.values())
                uptime = datetime.now() - app_state['start_time']
                memory_mb = get_memory_usage_estimate()
                
                stats_text = (
                    f"📊 <b>系统统计信息</b>\n\n"
                    f"📱 总号码数: {total_phones}\n"
                    f"🔍 总查询次数: {total_queries}\n"
                    f"👥 活跃用户: {len(user_data)}\n"
                    f"⏰ 运行时间: {str(uptime).split('.')[0]}\n"
                    f"💾 内存使用: {memory_mb:.1f} MB\n"
                    f"🧹 上次清理: {app_state['last_cleanup'].strftime('%H:%M:%S')}\n"
                    f"❤️ 上次健康检查: {app_state['last_health_check'].strftime('%H:%M:%S')}\n\n"
                    f"🗂️ <b>数据持久化</b>:\n"
                    f"📂 数据保留期: {PRODUCTION_CONFIG['DATA_RETENTION_DAYS']} 天\n"
                    f"💾 自动保存: 每 {PRODUCTION_CONFIG['DATA_SAVE_INTERVAL']//60} 分钟\n"
                    f"📦 备份保留: {PRODUCTION_CONFIG['BACKUP_RETENTION_DAYS']} 天\n\n"
                    f"🚀 版本: 1.8.0 Persistent Storage (Data Preservation)\n"
                    f"🔄 自动重启: {'✅ 已启用' if app_state['auto_restart_enabled'] else '❌ 已禁用'}"
                )
                
            send_telegram_message(chat_id, stats_text, message_id)
            
        elif command == '/duplicates':
            with data_lock:
                # 查找所有重复的号码（出现次数 > 1）
                duplicate_phones = [(phone, data) for phone, data in phone_registry.items() if data.get('count', 0) > 1]
                
                if not duplicate_phones:
                    send_telegram_message(
                        chat_id,
                        "🎉 <b>的好消息！</b>\n\n"
                        "暂时没有发现重复的电话号码",
                        message_id
                    )
                    return
                
                # 按重复次数排序
                duplicate_phones.sort(key=lambda x: x[1].get('count', 0), reverse=True)
                
                duplicates_text_parts = ["🔄 <b>重复号码统计</b>\n"]
                
                for i, (phone, data) in enumerate(duplicate_phones[:10], 1):  # 只显示前10个
                    analysis = analyze_phone_number(phone)
                    count = data.get('count', 0)
                    first_user_id = data.get('user_id')
                    first_user_name = get_user_display_name(first_user_id) if first_user_id else "未知用户"
                    first_time = data.get('timestamp', '')[:16]
                    
                    duplicates_text_parts.append(
                        f"{i}. 📞 {analysis['formatted']}\n"
                        f"   📍 {analysis['location']} | 📱 {analysis['carrier']}\n"
                        f"   🔢 重复 {count} 次\n"
                        f"   👤 首次: {first_user_name}\n"
                        f"   ⏰ 时间: {first_time}\n"
                    )
                
                if len(duplicate_phones) > 10:
                    duplicates_text_parts.append(f"\n… 还有 {len(duplicate_phones) - 10} 个重复号码")
                
                duplicates_text_parts.append(f"\n📊 总计: {len(duplicate_phones)} 个重复号码")
                
                duplicates_text = '\n'.join(duplicates_text_parts)
                send_telegram_message(chat_id, duplicates_text, message_id)
            
        elif command == '/clear':
            # 简化的管理员检查
            if user_id in admin_users or len(phone_registry) == 0:
                with data_lock:
                    phone_registry.clear()
                    user_data.clear()
                    gc.collect()
                
                send_telegram_message(
                    chat_id,
                    "🗑️ <b>数据清理完成</b>\n\n"
                    "所有电话号码记录和用户数据已清空",
                    message_id
                )
            else:
                send_telegram_message(
                    chat_id,
                    "⚠️ 此命令仅限管理员使用",
                    message_id
                )
        
        elif command == '/save':
            # 手动保存数据命令
            try:
                save_success = save_data_to_file()
                backup_success = create_backup()
                
                if save_success and backup_success:
                    send_telegram_message(
                        chat_id,
                        f"💾 <b>数据保存成功</b>\n\n"
                        f"📱 电话记录: {len(phone_registry)} 个\n"
                        f"👥 用户数据: {len(user_data)} 个\n"
                        f"⏰ 保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"📂 备份已创建",
                        message_id
                    )
                else:
                    send_telegram_message(
                        chat_id,
                        "❌ 数据保存失败，请查看日志",
                        message_id
                    )
            except Exception as e:
                logger.error(f"手动保存数据错误: {e}")
                send_telegram_message(
                    chat_id,
                    f"❌ 保存数据时发生错误: {str(e)}",
                    message_id
                )
        
        elif command == '/restart' and user_id in admin_users:
            send_telegram_message(chat_id, "🔄 正在重启机器人...", message_id)
            restart_application()
            
        else:
            send_telegram_message(
                chat_id,
                "❓ 未知命令，发送 /help 查看可用命令",
                message_id
            )
            
    except Exception as e:
        logger.error(f"处理命令错误: {e}")
        send_telegram_message(chat_id, "❌ 处理命令时发生错误")

class WebhookHandler(BaseHTTPRequestHandler):
    """Webhook处理器"""
    
    def do_GET(self):
        """处理GET请求（健康检查等）"""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            status = {
                'status': 'healthy',
                'uptime': str(datetime.now() - app_state['start_time']),
                'phones_count': len(phone_registry),
                'users_count': len(user_data),
                'memory_mb': get_memory_usage_estimate(),
                'version': '1.8.0 Persistent Storage (Data Preservation)',
                'auto_restart': app_state['auto_restart_enabled'],
                'timestamp': datetime.now().isoformat()
            }
            
            self.wfile.write(json.dumps(status, ensure_ascii=False).encode('utf-8'))
            
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>马来西亚电话号码机器人</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
                    .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                    h1 {{ color: #2196F3; }}
                    .status {{ color: #4CAF50; font-weight: bold; }}
                    .info {{ background: #E3F2FD; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🇲🇾 马来西亚电话号码机器人</h1>
                    <p class="status">✅ 服务正常运行</p>
                    
                    <div class="info">
                        <h3>📊 实时状态</h3>
                        <p>📱 已记录号码: {len(phone_registry)}</p>
                        <p>👥 活跃用户: {len(user_data)}</p>
                        <p>⏰ 运行时间: {datetime.now() - app_state['start_time']}</p>
                        <p>💾 内存使用: {get_memory_usage_estimate():.1f} MB</p>
                        <p>🔄 自动重启: {'已启用' if app_state['auto_restart_enabled'] else '已禁用'}</p>
                    </div>
                    
                    <div class="info">
                        <h3>🤖 Telegram机器人</h3>
                        <p>在Telegram中搜索机器人并发送电话号码即可使用</p>
                        <p>支持马来西亚手机号码和固话号码的智能识别</p>
                    </div>
                    
                    <div class="info">
                        <h3>🚀 版本信息</h3>
                        <p>版本: 1.8.0 Persistent Storage (Data Preservation)</p>
                        <p>更新时间: 2025-10-13 (v1.8.0 Enhanced Data Persistence)</p>
                        <p>作者: MiniMax Agent</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            self.wfile.write(html.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """处理POST请求（Telegram Webhook）"""
        try:
            if self.path == f'/webhook/{BOT_TOKEN}':
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                
                try:
                    data = json.loads(post_data.decode('utf-8'))
                    
                    if 'message' in data:
                        handle_text(data['message'])
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"ok": true}')
                    
                except json.JSONDecodeError as e:
                    logger.error(f"JSON解析错误: {e}")
                    self.send_response(400)
                    self.end_headers()
                    
            else:
                self.send_response(404)
                self.end_headers()
                
        except Exception as e:
            logger.error(f"POST请求处理错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """重写日志方法以避免重复日志"""
        pass

def setup_webhook():
    """设置Webhook"""
    try:
        webhook_url = os.getenv('WEBHOOK_URL')
        if not webhook_url:
            logger.warning("未设置WEBHOOK_URL环境变量，使用默认URL")
            webhook_url = "https://telegram-phone-bot-ouq9.onrender.com"
        
        full_webhook_url = f"{webhook_url}/webhook/{BOT_TOKEN}"
        
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        payload = {'url': full_webhook_url}
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get('ok'):
                logger.info(f"✅ Webhook设置成功: {full_webhook_url}")
                return True
            else:
                logger.error(f"❌ Webhook设置失败: {result}")
                return False
                
    except Exception as e:
        logger.error(f"设置Webhook时发生错误: {e}")
        return False

def run_server():
    """运行HTTP服务器"""
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建数据目录
    ensure_data_directories()
    
    # 加载已保存的数据
    logger.info("📂 正在加载历史数据...")
    load_data_from_file()
    
    # 启动数据清理线程
    cleanup_thread = threading.Thread(target=data_cleanup_worker, daemon=True)
    cleanup_thread.start()
    
    # 启动数据保存线程
    save_thread = threading.Thread(target=data_save_worker, daemon=True)
    save_thread.start()
    
    # 设置Webhook
    setup_webhook()
    
    port = int(os.getenv('PORT', 10000))
    httpd = None
    heartbeat_thread = None
    
    # 记录启动信息
    logger.info("=" * 60)
    logger.info("🚀 马来西亚电话号码机器人已启动 (长期运行版)")
    logger.info(f"📦 版本: 1.8.0 Persistent Storage (Data Preservation)")
    logger.info(f"🌐 端口: {port}")
    logger.info(f"💾 内存估算: {get_memory_usage_estimate()} MB")
    logger.info(f"⏰ 启动时间: {app_state['start_time']}")
    logger.info("🔧 配置:")
    logger.info(f"   - 数据保留: {PRODUCTION_CONFIG['DATA_RETENTION_DAYS']} 天")
    logger.info(f"   - 清理间隔: {PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']} 秒")
    logger.info(f"   - 最大内存: {PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']} MB")
    logger.info(f"   - 最大记录: {PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']} 个")
    logger.info(f"   - 自动重启: {'已启用' if app_state['auto_restart_enabled'] else '已禁用'}")
    logger.info("=" * 60)
    
    try:
        httpd = HTTPServer(('0.0.0.0', port), WebhookHandler)
        logger.info(f"🌐 HTTP服务器启动成功，监听端口 {port}")
        
        # 启动心跳监控
        heartbeat_thread = threading.Thread(target=heartbeat_monitor, daemon=True)
        heartbeat_thread.start()
        
        httpd.serve_forever()
        
    except KeyboardInterrupt:
        logger.info("🛑 收到中断信号")
    except Exception as e:
        logger.error(f"服务器运行错误: {e}")
    finally:
        logger.info("🛑 开始优雅停机...")
        app_state['running'] = False
        
        # 最后保存一次数据
        logger.info("💾 执行最终数据保存...")
        try:
            save_data_to_file()
            create_backup()
        except Exception as e:
            logger.error(f"最终保存数据失败: {e}")
        
        logger.info("关闭HTTP服务器...")
        try:
            if httpd:
                httpd.shutdown()
        except Exception as e:
            logger.error(f"关闭HTTP服务器失败: {e}")
        
        logger.info("等待线程结束...")
        try:
            cleanup_thread.join(timeout=10)
            save_thread.join(timeout=10)
            if heartbeat_thread:
                heartbeat_thread.join(timeout=5)
        except Exception as e:
            logger.error(f"等待线程结束失败: {e}")
        
        logger.info("✅ 优雅停机完成")

def heartbeat_monitor():
    """心跳监控线程"""
    logger.info("❤️ 心跳监控线程已启动")
    
    while app_state['running']:
        try:
            time.sleep(300)  # 每5分钟一次心跳
            
            if not app_state['running']:
                break
                
            # 发送心跳
            send_heartbeat()
            
            # 定期强制垃圾回收
            gc.collect()
            
        except Exception as e:
            logger.error(f"心跳监控错误: {e}")
            time.sleep(60)
    
    logger.info("心跳监控线程已停止")

if __name__ == '__main__':
    try:
        run_server()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        sys.exit(1)
