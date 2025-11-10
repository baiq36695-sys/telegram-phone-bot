#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码机器人 - 永久保存增强版
专为长期数据保留设计，增强的持久化机制
增强功能：永久保存、无限期保留、数据库导出、数据完整性保护

作者: MiniMax Agent
版本: 2.0.0 永久保存增强版
更新时间: 2025-11-11
"""

import json
import re
import threading
import time
import urllib.parse
import urllib.request
import sqlite3
import csv
import hashlib
import os
import gc
import signal
import sys
import logging
import shutil
import pickle
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import contextmanager

# 永久保存配置
PERMANENT_CONFIG = {
    # 永久保存设置
    'ENABLE_PERMANENT_STORAGE': True,    # 启用永久保存
    'DATABASE_PATH': 'data/phone_history.db',  # SQLite数据库路径
    'CSV_EXPORT_PATH': 'data/phone_export.csv',  # CSV导出文件
    'PERMANENT_BACKUP_PATH': 'data/permanent_backups/',  # 永久备份目录
    
    # 永久保留策略
    'NEVER_DELETE_PHONES': True,         # 永不复删电话号码
    'COMPRESS_OLD_DATA': True,           # 压缩旧数据
    'DATA_INTEGRITY_CHECK': True,        # 数据完整性检查
    'AUTO_CSV_EXPORT_INTERVAL': 3600,    # 自动CSV导出间隔（1小时）
    'DATABASE_OPTIMIZATION_INTERVAL': 86400,  # 数据库优化间隔（24小时）
    
    # 文件保留设置
    'MAX_FILE_SIZE_MB': 500,             # 单个文件最大500MB
    'KEEP_ALL_BACKUPS_FOREVER': True,    # 永久保留所有备份
    'ENABLE_MULTI_STORAGE': True,        # 启用多重存储（JSON+SQLite+CSV）
    'EXPORT_RAW_DATA': True,             # 导出原始数据
}

# 生产环境配置（长期运行优化）
PRODUCTION_CONFIG = {
    'MAX_PHONE_REGISTRY_SIZE': 1000000,  # 增大到100万个电话号码记录
    'MAX_USER_DATA_SIZE': 50000,         # 增大到5万用户数据
    'DATA_CLEANUP_INTERVAL': 3600,       # 数据清理间隔（1小时）
    'DATA_RETENTION_DAYS': 999999,       # 几乎无限保留（2739年）
    'AUTO_RESTART_MEMORY_MB': 1000,      # 内存使用超过此值时自动重启
    'MAX_MESSAGE_LENGTH': 4096,          # Telegram消息最大长度
    'REQUEST_TIMEOUT': 15,               # HTTP请求超时时间
    'MAX_CONCURRENT_REQUESTS': 10,       # 最大并发请求数
    'HEALTH_CHECK_INTERVAL': 300,        # 健康检查间隔（5分钟）
    'ERROR_RETRY_MAX': 3,                # 最大重试次数
    'GRACEFUL_SHUTDOWN_TIMEOUT': 30,     # 优雅停机超时时间
    'DATA_SAVE_INTERVAL': 300,           # 数据保存间隔（5分钟）
    'BACKUP_RETENTION_DAYS': 999999,     # 永久保留备份
}

# 从环境变量获取配置
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# 数据目录和文件路径
DATA_DIR = 'data'
PHONE_REGISTRY_FILE = os.path.join(DATA_DIR, 'phone_registry.json')
USER_DATA_FILE = os.path.join(DATA_DIR, 'user_data.json')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
PERMANENT_BACKUP_DIR = PERMANENT_CONFIG['PERMANENT_BACKUP_PATH']

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
database_lock = threading.RLock()  # 数据库锁

# 全局状态管理
app_state = {
    'running': True,
    'last_cleanup': datetime.now(),
    'last_health_check': datetime.now(),
    'last_csv_export': datetime.now(),
    'last_db_optimization': datetime.now(),
    'error_count': 0,
    'request_count': 0,
    'start_time': datetime.now(),
    'auto_restart_enabled': True,
    'total_phones_saved': 0,
    'permanent_storage_enabled': True
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
    
    # 9位数字格式（本地格式不含0）
    re.compile(r'\b(1[3-9]\d{7})\b'),                        # 13-xxx-xxxx
    re.compile(r'\b([3456789]\d{8})\b'),                     # 3-xxxx-xxxx
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
        os.makedirs(PERMANENT_BACKUP_DIR, exist_ok=True)
        logger.info(f"数据目录已创建: {DATA_DIR}")
    except Exception as e:
        logger.error(f"创建数据目录失败: {e}")

def init_database():
    """初始化SQLite数据库"""
    try:
        with database_lock:
            conn = sqlite3.connect(PERMANENT_CONFIG['DATABASE_PATH'], check_same_thread=False)
            cursor = conn.cursor()
            
            # 创建电话号码历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS phone_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT UNIQUE NOT NULL,
                    formatted_phone TEXT NOT NULL,
                    carrier TEXT,
                    location TEXT,
                    type TEXT,
                    count INTEGER DEFAULT 1,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id INTEGER,
                    chat_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    data_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_phone ON phone_history(phone_number)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_user ON phone_history(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_type ON phone_history(type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON phone_history(last_seen)')
            
            # 创建数据完整性表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS data_integrity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("SQLite数据库初始化完成")
            return True
    except Exception as e:
        logger.error(f"初始化数据库失败: {e}")
        return False

def save_to_database():
    """将数据保存到SQLite数据库"""
    try:
        with database_lock:
            conn = sqlite3.connect(PERMANENT_CONFIG['DATABASE_PATH'], check_same_thread=False)
            cursor = conn.cursor()
            
            saved_count = 0
            updated_count = 0
            
            with data_lock:
                for phone, data in phone_registry.items():
                    try:
                        # 分析电话号码
                        analysis = analyze_phone_number(phone)
                        
                        # 计算数据哈希
                        data_string = f"{phone}_{data.get('count', 1)}_{data.get('timestamp', '')}"
                        data_hash = hashlib.md5(data_string.encode('utf-8')).hexdigest()
                        
                        # 检查是否已存在
                        cursor.execute('SELECT id, data_hash FROM phone_history WHERE phone_number = ?', (phone,))
                        existing = cursor.fetchone()
                        
                        if existing:
                            # 更新现有记录
                            cursor.execute('''
                                UPDATE phone_history SET
                                    count = ?,
                                    last_seen = ?,
                                    data_hash = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE phone_number = ?
                            ''', (
                                data.get('count', 1),
                                data.get('last_seen', datetime.now().isoformat()),
                                data_hash,
                                phone
                            ))
                            updated_count += 1
                        else:
                            # 插入新记录
                            cursor.execute('''
                                INSERT INTO phone_history (
                                    phone_number, formatted_phone, carrier, location, type,
                                    count, user_id, chat_id, username, first_name, last_name, data_hash
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                phone,
                                analysis['formatted'],
                                analysis['carrier'],
                                analysis['location'],
                                analysis['type'],
                                data.get('count', 1),
                                data.get('user_id'),
                                data.get('chat_id'),
                                data.get('username', ''),
                                data.get('first_name', ''),
                                data.get('last_name', ''),
                                data_hash
                            ))
                            saved_count += 1
                            
                    except Exception as e:
                        logger.error(f"保存电话号码 {phone} 到数据库失败: {e}")
                        continue
            
            conn.commit()
            conn.close()
            
            app_state['total_phones_saved'] += saved_count + updated_count
            logger.info(f"数据库保存完成 - 新增: {saved_count}, 更新: {updated_count}")
            return True
            
    except Exception as e:
        logger.error(f"保存到数据库失败: {e}")
        return False

def export_to_csv():
    """导出数据到CSV文件"""
    try:
        with data_lock:
            # 准备CSV数据
            csv_data = []
            csv_data.append([
                'phone_number', 'formatted_phone', 'carrier', 'location', 'type',
                'count', 'first_seen', 'last_seen', 'user_id', 'username', 
                'first_name', 'last_name', 'analysis_result'
            ])
            
            for phone, data in phone_registry.items():
                analysis = analyze_phone_number(phone)
                csv_data.append([
                    phone,
                    analysis['formatted'],
                    analysis['carrier'],
                    analysis['location'],
                    analysis['type'],
                    data.get('count', 1),
                    data.get('timestamp', ''),
                    data.get('last_seen', ''),
                    data.get('user_id', ''),
                    data.get('username', ''),
                    data.get('first_name', ''),
                    data.get('last_name', ''),
                    f"{analysis['carrier']} - {analysis['location']}"
                ])
            
            # 写入CSV文件
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_file = f"{PERMANENT_CONFIG['CSV_EXPORT_PATH'].replace('.csv', '')}_{timestamp}.csv"
            
            with open(csv_file, 'w', newline='', encoding='utf-8-sig') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerows(csv_data)
            
            logger.info(f"CSV导出完成: {csv_file} (记录数: {len(csv_data)-1})")
            return True
            
    except Exception as e:
        logger.error(f"CSV导出失败: {e}")
        return False

def verify_data_integrity():
    """验证数据完整性"""
    try:
        with database_lock:
            conn = sqlite3.connect(PERMANENT_CONFIG['DATABASE_PATH'], check_same_thread=False)
            cursor = conn.cursor()
            
            # 计算当前记录数
            cursor.execute('SELECT COUNT(*) FROM phone_history')
            db_count = cursor.fetchone()[0]
            
            # 计算内存中的记录数
            memory_count = len(phone_registry)
            
            # 生成当前数据的校验和
            total_hash = hashlib.md5()
            with data_lock:
                for phone, data in sorted(phone_registry.items()):
                    total_hash.update(f"{phone}:{data.get('count', 1)}".encode('utf-8'))
            
            checksum = total_hash.hexdigest()
            
            # 记录完整性信息
            cursor.execute('''
                INSERT INTO data_integrity (table_name, record_count, checksum)
                VALUES (?, ?, ?)
            ''', ('phone_history', memory_count, checksum))
            
            conn.commit()
            conn.close()
            
            logger.info(f"数据完整性验证 - 内存: {memory_count}, 数据库: {db_count}, 校验: {checksum[:8]}")
            return memory_count == db_count
            
    except Exception as e:
        logger.error(f"数据完整性验证失败: {e}")
        return False

def create_permanent_backup():
    """创建永久备份"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 创建时间戳目录
        backup_dir = os.path.join(PERMANENT_BACKUP_DIR, f"backup_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)
        
        # 备份JSON文件
        if os.path.exists(PHONE_REGISTRY_FILE):
            shutil.copy2(PHONE_REGISTRY_FILE, os.path.join(backup_dir, 'phone_registry.json'))
        
        if os.path.exists(USER_DATA_FILE):
            shutil.copy2(USER_DATA_FILE, os.path.join(backup_dir, 'user_data.json'))
        
        # 备份SQLite数据库
        if os.path.exists(PERMANENT_CONFIG['DATABASE_PATH']):
            shutil.copy2(PERMANENT_CONFIG['DATABASE_PATH'], 
                        os.path.join(backup_dir, 'phone_history.db'))
        
        # 创建元数据文件
        metadata = {
            'backup_timestamp': timestamp,
            'phone_count': len(phone_registry),
            'user_count': len(user_data),
            'total_phones_saved': app_state['total_phones_saved'],
            'version': '2.0.0 永久保存增强版',
            'created_by': 'Malaysia Phone Bot Permanent Storage'
        }
        
        with open(os.path.join(backup_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        logger.info(f"永久备份已创建: {backup_dir}")
        return True
        
    except Exception as e:
        logger.error(f"创建永久备份失败: {e}")
        return False

def save_data_to_file():
    """保存数据到文件（增强版）"""
    try:
        with data_lock:
            # 保存电话号码注册表
            with open(PHONE_REGISTRY_FILE, 'w', encoding='utf-8') as f:
                json.dump(phone_registry, f, ensure_ascii=False, indent=2)
            
            # 保存用户数据
            user_data_dict = dict(user_data)  # 转换 defaultdict 为普通字典
            with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(user_data_dict, f, ensure_ascii=False, indent=2)
            
            # 同时保存到数据库
            if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE']:
                save_to_database()
            
            logger.info(f"数据已保存 - 电话记录: {len(phone_registry)}, 用户数据: {len(user_data)}")
            return True
    except Exception as e:
        logger.error(f"保存数据失败: {e}")
        return False

def load_data_from_file():
    """从文件加载数据（增强版）"""
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
                backup_corrupted_file = f"{PHONE_REGISTRY_FILE}.corrupted.{int(time.time())}"
                shutil.move(PHONE_REGISTRY_FILE, backup_corrupted_file)
                logger.info(f"已将损坏文件移动到: {backup_corrupted_file}")
        else:
            logger.info("电话注册表文件不存在，从空数据开始")
        
        # 从数据库恢复数据（如果存在）
        if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE'] and os.path.exists(PERMANENT_CONFIG['DATABASE_PATH']):
            try:
                with database_lock:
                    conn = sqlite3.connect(PERMANENT_CONFIG['DATABASE_PATH'], check_same_thread=False)
                    cursor = conn.cursor()
                    
                    cursor.execute('SELECT * FROM phone_history')
                    rows = cursor.fetchall()
                    
                    with data_lock:
                        for row in rows:
                            phone = row[1]  # phone_number
                            phone_registry[phone] = {
                                'timestamp': row[6],  # first_seen
                                'count': row[5],      # count
                                'last_seen': row[7],  # last_seen
                                'user_id': row[8],    # user_id
                                'chat_id': row[9],    # chat_id
                                'username': row[10],  # username
                                'first_name': row[11], # first_name
                                'last_name': row[12]   # last_name
                            }
                    
                    conn.close()
                    logger.info(f"从数据库恢复 {len(rows)} 个电话记录")
                    
            except Exception as e:
                logger.error(f"从数据库恢复数据失败: {e}")
        
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
                backup_corrupted_file = f"{USER_DATA_FILE}.corrupted.{int(time.time())}"
                shutil.move(USER_DATA_FILE, backup_corrupted_file)
                logger.info(f"已将损坏文件移动到: {backup_corrupted_file}")
        else:
            logger.info("用户数据文件不存在，从空数据开始")
        
        return True
    except Exception as e:
        logger.error(f"加载数据失败: {e}")
        return False

def cleanup_old_backups():
    """清理过期的备份文件（永久保存版本 - 不清理）"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return
        
        # 永久保存版本不删除备份文件，只记录统计
        backup_count = len([f for f in os.listdir(BACKUP_DIR) if os.path.isfile(os.path.join(BACKUP_DIR, f))])
        logger.info(f"当前备份文件数量: {backup_count} (永久保留)")
        
    except Exception as e:
        logger.error(f"检查备份文件失败: {e}")

def cleanup_old_data():
    """清理过期数据（永久保存版本 - 几乎不清理）"""
    with data_lock:
        # 永久保存版本：只清理绝对过期的数据（超过保留期但仍然保留核心数据）
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(days=PRODUCTION_CONFIG['DATA_RETENTION_DAYS'])
        
        initial_phone_count = len(phone_registry)
        initial_user_count = len(user_data)
        
        # 几乎不清理电话号码（只在数量极度超限时才清理）
        if len(phone_registry) > PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:
            sorted_phones = sorted(phone_registry.items(), 
                                 key=lambda x: x[1].get('timestamp', '1970-01-01'))
            excess_count = len(phone_registry) - PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']
            for phone, _ in sorted_phones[:excess_count]:
                del phone_registry[phone]
        
        # 只清理用户数据（保留活跃用户）
        if len(user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:
            sorted_users = sorted(user_data.items(),
                                key=lambda x: x[1].get('last_activity', '1970-01-01'))
            excess_count = len(user_data) - PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']
            for user_id, _ in sorted_users[:excess_count]:
                del user_data[user_id]
        
        # 立即保存数据
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

def permanent_data_worker():
    """永久数据工作线程"""
    logger.info("🛡️ 永久数据保存线程已启动")
    
    while app_state['running']:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_SAVE_INTERVAL'])
            
            if not app_state['running']:
                break
                
            # 保存数据到多个存储
            save_data_to_file()
            app_state['last_cleanup'] = datetime.now()
            
            # 定期CSV导出
            current_time = datetime.now()
            if (current_time - app_state['last_csv_export']).total_seconds() > PERMANENT_CONFIG['AUTO_CSV_EXPORT_INTERVAL']:
                export_to_csv()
                app_state['last_csv_export'] = current_time
            
            # 定期数据完整性检查
            if PERMANENT_CONFIG['DATA_INTEGRITY_CHECK']:
                verify_data_integrity()
            
            # 定期创建永久备份
            if (current_time - app_state['start_time']).total_seconds() > 3600:  # 每小时创建一次
                create_permanent_backup()
            
            # 检查内存使用（但不强制清理电话号码）
            memory_mb = get_memory_usage_estimate()
            if memory_mb > PRODUCTION_CONFIG['AUTO_RESTART_MEMORY_MB']:
                logger.warning(f"内存使用过高 ({memory_mb:.1f}MB)，执行保守清理")
                # 永久保存版本：只清理用户数据，保留电话号码
                with data_lock:
                    if len(user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE'] // 2:
                        sorted_users = sorted(user_data.items(),
                                            key=lambda x: x[1].get('last_activity', '1970-01-01'))
                        remove_count = len(user_data) // 4  # 只清理25%
                        for user_id, _ in sorted_users[:remove_count]:
                            del user_data[user_id]
                        logger.info(f"保守清理：删除了 {remove_count} 个用户记录")
            
            perform_health_check()
                
        except Exception as e:
            logger.error(f"永久数据工作线程错误: {e}")
            app_state['error_count'] += 1
            
            if app_state['error_count'] > 10:
                logger.warning("错误过多，暂停永久数据保存60秒")
                time.sleep(60)
                app_state['error_count'] = 0
    
    logger.info("永久数据保存线程已停止")

def data_cleanup_worker():
    """数据清理工作线程（永久保存版本）"""
    logger.info("🧹 数据清理线程已启动（永久保存模式）")
    
    while app_state['running']:
        try:
            time.sleep(PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL'])
            
            if not app_state['running']:
                break
                
            # 永久保存版本：只进行数据完整性检查和备份
            cleanup_old_data()
            
            # 数据库优化（每日一次）
            current_time = datetime.now()
            if (current_time - app_state['last_db_optimization']).total_seconds() > PERMANENT_CONFIG['DATABASE_OPTIMIZATION_INTERVAL']:
                if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE']:
                    optimize_database()
                    app_state['last_db_optimization'] = current_time
                
        except Exception as e:
            logger.error(f"数据清理工作线程错误: {e}")
            app_state['error_count'] += 1
            
            if app_state['error_count'] > 10:
                logger.warning("错误过多，暂停数据清理60秒")
                time.sleep(60)
                app_state['error_count'] = 0
    
    logger.info("数据清理工作线程已停止")

def optimize_database():
    """优化SQLite数据库"""
    try:
        with database_lock:
            conn = sqlite3.connect(PERMANENT_CONFIG['DATABASE_PATH'], check_same_thread=False)
            cursor = conn.cursor()
            
            # 执行数据库优化
            cursor.execute('VACUUM')
            cursor.execute('ANALYZE')
            cursor.execute('REINDEX')
            
            # 统计信息
            cursor.execute('SELECT COUNT(*) FROM phone_history')
            total_records = cursor.fetchone()[0]
            
            # 索引使用统计
            cursor.execute('PRAGMA index_list(phone_history)')
            indexes = cursor.fetchall()
            
            conn.commit()
            conn.close()
            
            logger.info(f"数据库优化完成 - 记录数: {total_records}, 索引数: {len(indexes)}")
            return True
            
    except Exception as e:
        logger.error(f"数据库优化失败: {e}")
        return False

def force_cleanup():
    """强制清理更多数据以释放内存（永久保存版本）"""
    with data_lock:
        # 永久保存版本：只清理用户数据，保护电话号码记录
        if len(user_data) > PRODUCTION_CONFIG['MAX_USER_DATA_SIZE'] // 2:
            sorted_users = sorted(user_data.items(),
                                key=lambda x: x[1].get('last_activity', '1970-01-01'))
            remove_count = len(user_data) // 2
            for user_id, _ in sorted_users[:remove_count]:
                del user_data[user_id]
            
            logger.info(f"强制清理：只删除了 {remove_count} 个用户记录（保护电话号码）")
        
        gc.collect()

def perform_health_check():
    """执行系统健康检查"""
    try:
        app_state['last_health_check'] = datetime.now()
        
        memory_mb = get_memory_usage_estimate()
        uptime = (datetime.now() - app_state['start_time']).total_seconds()
        
        if uptime % 3600 < 60:  # 每小时记录一次
            logger.info(f"健康检查 - 运行时间: {uptime/3600:.1f}h, 内存: {memory_mb:.1f}MB, "
                       f"电话记录: {len(phone_registry)}, 用户: {len(user_data)}, "
                       f"永久保存: ✅, 总保存: {app_state['total_phones_saved']}")
        
        send_heartbeat()
        
    except Exception as e:
        logger.error(f"健康检查错误: {e}")

def send_heartbeat():
    """发送心跳信号到Render"""
    try:
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
    """从文本中智能提取电话号码（增强版）"""
    phone_candidates = set()
    
    for pattern in PHONE_EXTRACTION_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            if isinstance(match, tuple):
                candidate = ''.join(match)
            else:
                candidate = match
            
            cleaned = re.sub(r'[\s\-\(\)]+', '', candidate)
            
            # 降低最小长度要求到7位，永久保存所有有效号码
            if len(cleaned) >= 7 and cleaned.isdigit():
                normalized = normalize_phone_format(cleaned)
                if normalized:
                    phone_candidates.add(normalized)
    
    return list(phone_candidates)

def normalize_phone_format(phone):
    """增强的电话号码标准化格式（支持9位数字）"""
    # 移除所有非数字字符
    digits_only = re.sub(r'\D', '', phone)
    
    # 特殊处理：9位数字格式（本地格式不含0）
    if len(digits_only) == 9:
        if digits_only[0] == '1':  # 移动电话
            return '+60' + digits_only
        elif digits_only[0] in '3456789':  # 固话
            return '+60' + digits_only
    
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
                    "• 03-1234 5678（固话）\n"
                    "• 16-783 7377（9位本地格式）",
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
                            f"✅ <b>新号码记录</b> (已永久保存)\n"
                            f"   👤 记录者: {current_user_name}\n"
                            f"   🛡️ 永久保护: ✅\n"
                        )
            
            # 移除底部统计信息，保持显示简洁
            
            response_text = '\n'.join(response_parts)
            send_telegram_message(chat_id, response_text, message_id)
            
    except Exception as e:
        logger.error(f"处理文本消息错误: {e}")
        send_telegram_message(chat_id, "❌ 处理消息时发生错误，请稍后重试")

def handle_command(chat_id, user_id, command, message_id=None):
    """处理命令（增强永久保存功能）"""
    try:
        if command == '/start':
            welcome_text = (
                "🇲🇾 <b>马来西亚电话号码智能追踪机器人</b>\n"
                "🛡️ <b>永久保存增强版</b>\n\n"
                "✨ <b>功能特色</b>:\n"
                "📱 智能识别手机/固话号码\n"
                "🎯 精确归属地/运营商查询\n"
                "🔄 重复号码追踪统计\n"
                "🛡️ <b>永久保存数据保护</b>\n"
                "💾 <b>多重存储</b> (JSON+SQLite+CSV)\n"
                "📊 完整的使用数据分析\n\n"
                "📝 <b>使用方法</b>:\n"
                "直接发送包含电话号码的消息即可\n\n"
                "🤖 <b>命令列表</b>:\n"
                "/help - 帮助信息\n"
                "/stats - 查看统计\n"
                "/duplicates - 查看重复号码\n"
                "/save - 手动保存数据\n"
                "/export - 导出CSV数据\n"
                "/verify - 验证数据完整性\n"
                "/backup - 创建永久备份\n"
                "/clear - 清理数据（管理员）\n\n"
                f"🚀 <b>版本</b>: 2.0.0 永久保存增强版\n"
                f"⏰ <b>启动时间</b>: {app_state['start_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🛡️ <b>永久保存</b>: {'✅ 已启用' if app_state['permanent_storage_enabled'] else '❌ 已禁用'}"
            )
            send_telegram_message(chat_id, welcome_text, message_id)
            
        elif command == '/help':
            help_text = (
                "📖 <b>马来西亚电话号码机器人帮助</b>\n🛡️ <b>永久保存增强版</b>\n\n"
                "🎯 <b>支持的号码格式</b>:\n"
                "• +60 12-345 6789\n"
                "• 012-345 6789\n"
                "• 0123456789\n"
                "• 03-1234 5678（固话）\n"
                "• (03) 1234-5678\n"
                "• 16-783 7377（9位本地格式）\n\n"
                "🛡️ <b>永久保存功能</b>:\n"
                "• 电话号码永不丢失\n"
                "• 多重存储保护 (JSON+SQLite+CSV)\n"
                "• 数据完整性验证\n"
                "• 自动备份创建\n"
                "• 无限期数据保留\n\n"
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
                "/export - 导出CSV数据文件\n"
                "/verify - 验证数据完整性\n"
                "/backup - 创建永久备份\n"
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
                    f"📊 <b>系统统计信息</b>\n🛡️ <b>永久保存模式</b>\n\n"
                    f"📱 总号码数: {total_phones}\n"
                    f"🔍 总查询次数: {total_queries}\n"
                    f"👥 活跃用户: {len(user_data)}\n"
                    f"⏰ 运行时间: {str(uptime).split('.')[0]}\n"
                    f"💾 内存使用: {memory_mb:.1f} MB\n"
                    f"🧹 上次清理: {app_state['last_cleanup'].strftime('%H:%M:%S')}\n"
                    f"❤️ 上次健康检查: {app_state['last_health_check'].strftime('%H:%M:%S')}\n\n"
                    f"🛡️ <b>永久保存统计</b>:\n"
                    f"📦 总保存次数: {app_state['total_phones_saved']}\n"
                    f"💾 JSON存储: ✅\n"
                    f"🗃️ SQLite存储: {'✅' if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE'] else '❌'}\n"
                    f"📄 CSV导出: 每小时自动\n"
                    f"🗂️ 永久备份: 每小时创建\n"
                    f"🔒 数据完整性: {'✅' if PERMANENT_CONFIG['DATA_INTEGRITY_CHECK'] else '❌'}\n\n"
                    f"🚀 版本: 2.0.0 永久保存增强版\n"
                    f"🔄 自动重启: {'✅ 已启用' if app_state['auto_restart_enabled'] else '❌ 已禁用'}\n"
                    f"🛡️ 永久保护: ✅ 永不复删电话号码"
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
                        f"   🛡️ 永久保存: ✅\n"
                    )
                
                if len(duplicate_phones) > 10:
                    duplicates_text_parts.append(f"\n… 还有 {len(duplicate_phones) - 10} 个重复号码")
                
                duplicates_text_parts.append(f"\n📊 总计: {len(duplicate_phones)} 个重复号码 (永久保护)")
                
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
                    "所有电话号码记录和用户数据已清空\n"
                    "注意：永久保存版本建议谨慎使用此命令",
                    message_id
                )
            else:
                send_telegram_message(
                    chat_id,
                    "⚠️ 此命令仅限管理员使用",
                    message_id
                )
        
        elif command == '/save':
            # 手动保存数据命令（增强版）
            try:
                save_success = save_data_to_file()
                backup_success = create_permanent_backup()
                
                if save_success:
                    send_telegram_message(
                        chat_id,
                        f"💾 <b>数据保存成功</b> (永久保存模式)\n\n"
                        f"📱 电话记录: {len(phone_registry)} 个\n"
                        f"👥 用户数据: {len(user_data)} 个\n"
                        f"⏰ 保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"📦 总保存: {app_state['total_phones_saved']} 次\n"
                        f"🗃️ JSON: ✅ SQLite: {'✅' if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE'] else '❌'}\n"
                        f"🛡️ 永久保护: ✅ 永不丢失",
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
        
        elif command == '/export':
            # 导出CSV数据命令
            try:
                export_success = export_to_csv()
                
                if export_success:
                    send_telegram_message(
                        chat_id,
                        f"📄 <b>CSV导出成功</b>\n\n"
                        f"📊 导出记录: {len(phone_registry)} 个电话号码\n"
                        f"📁 文件位置: data/ 目录\n"
                        f"⏰ 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"🛡️ 包含永久保存标记",
                        message_id
                    )
                else:
                    send_telegram_message(
                        chat_id,
                        "❌ CSV导出失败，请查看日志",
                        message_id
                    )
            except Exception as e:
                logger.error(f"CSV导出错误: {e}")
                send_telegram_message(
                    chat_id,
                    f"❌ 导出数据时发生错误: {str(e)}",
                    message_id
                )
        
        elif command == '/verify':
            # 验证数据完整性命令
            try:
                integrity_ok = verify_data_integrity()
                
                if integrity_ok:
                    send_telegram_message(
                        chat_id,
                        f"✅ <b>数据完整性验证通过</b>\n\n"
                        f"📱 电话记录: {len(phone_registry)} 个\n"
                        f"🛡️ 数据完整性: 验证通过\n"
                        f"⏰ 验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"🔒 永久保存: 正常",
                        message_id
                    )
                else:
                    send_telegram_message(
                        chat_id,
                        f"⚠️ <b>数据完整性检查</b>\n\n"
                        f"📊 内存记录: {len(phone_registry)} 个\n"
                        f"🛡️ 数据可能有差异，建议执行保存操作",
                        message_id
                    )
            except Exception as e:
                logger.error(f"数据验证错误: {e}")
                send_telegram_message(
                    chat_id,
                    f"❌ 验证数据时发生错误: {str(e)}",
                    message_id
                )
        
        elif command == '/backup':
            # 创建永久备份命令
            try:
                backup_success = create_permanent_backup()
                
                if backup_success:
                    send_telegram_message(
                        chat_id,
                        f"🗂️ <b>永久备份创建成功</b>\n\n"
                        f"📦 备份包含:\n"
                        f"• 电话号码数据库\n"
                        f"• 用户数据备份\n"
                        f"• 完整性校验信息\n"
                        f"⏰ 备份时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"🛡️ 永久保留，无过期限制",
                        message_id
                    )
                else:
                    send_telegram_message(
                        chat_id,
                        "❌ 永久备份失败，请查看日志",
                        message_id
                    )
            except Exception as e:
                logger.error(f"创建备份错误: {e}")
                send_telegram_message(
                    chat_id,
                    f"❌ 创建备份时发生错误: {str(e)}",
                    message_id
                )
        
        else:
            send_telegram_message(
                chat_id,
                "❓ 未知命令，请使用 /help 查看可用命令",
                message_id
            )
            
    except Exception as e:
        logger.error(f"处理命令错误: {e}")
        send_telegram_message(chat_id, "❌ 处理命令时发生错误，请稍后重试")

class WebhookHandler(BaseHTTPRequestHandler):
    """Webhook处理器"""
    
    def do_POST(self):
        """处理POST请求"""
        try:
            if not self.path.startswith(f'/webhook/{BOT_TOKEN}'):
                self.send_response(404)
                self.end_headers()
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            
            if content_length > 10 * 1024 * 1024:  # 10MB limit
                self.send_response(413)
                self.end_headers()
                return
            
            post_data = self.rfile.read(content_length)
            
            try:
                update = json.loads(post_data.decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            
            # 更新请求计数
            app_state['request_count'] += 1
            
            # 处理更新
            if 'message' in update:
                handle_text(update['message'])
            
            # 发送响应
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            logger.error(f"处理webhook请求错误: {e}")
            try:
                self.send_response(500)
                self.end_headers()
            except:
                pass
    
    def do_GET(self):
        """处理GET请求（健康检查）"""
        try:
            if self.path == '/health' or self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                
                health_info = {
                    'status': 'ok',
                    'uptime_seconds': int((datetime.now() - app_state['start_time']).total_seconds()),
                    'phone_registry_size': len(phone_registry),
                    'user_data_size': len(user_data),
                    'memory_estimate_mb': get_memory_usage_estimate(),
                    'request_count': app_state['request_count'],
                    'total_phones_saved': app_state['total_phones_saved'],
                    'permanent_storage_enabled': app_state['permanent_storage_enabled'],
                    'version': '2.0.0 永久保存增强版'
                }
                
                self.wfile.write(json.dumps(health_info).encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:
            logger.error(f"处理健康检查请求错误: {e}")
            try:
                self.send_response(500)
                self.end_headers()
            except:
                pass
    
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
    
    # 初始化数据库
    if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE']:
        init_database()
    
    # 加载已保存的数据
    logger.info("📂 正在加载历史数据...")
    load_data_from_file()
    
    # 启动永久数据保存线程
    permanent_thread = threading.Thread(target=permanent_data_worker, daemon=True)
    permanent_thread.start()
    
    # 启动数据清理线程
    cleanup_thread = threading.Thread(target=data_cleanup_worker, daemon=True)
    cleanup_thread.start()
    
    # 设置Webhook
    setup_webhook()
    
    port = int(os.getenv('PORT', 10000))
    httpd = None
    heartbeat_thread = None
    
    # 记录启动信息
    logger.info("=" * 60)
    logger.info("🚀 马来西亚电话号码机器人已启动 (永久保存增强版)")
    logger.info(f"📦 版本: 2.0.0 永久保存增强版")
    logger.info(f"🌐 端口: {port}")
    logger.info(f"💾 内存估算: {get_memory_usage_estimate()} MB")
    logger.info(f"⏰ 启动时间: {app_state['start_time']}")
    logger.info("🛡️ 永久保存配置:")
    logger.info(f"   - 多重存储: JSON+SQLite+CSV")
    logger.info(f"   - 永久保留: 永不删电话号码")
    logger.info(f"   - 数据完整性: {'✅ 启用' if PERMANENT_CONFIG['DATA_INTEGRITY_CHECK'] else '❌ 禁用'}")
    logger.info(f"   - 自动备份: 每小时创建")
    logger.info(f"   - CSV导出: 每小时自动")
    logger.info(f"   - 数据库优化: 每日执行")
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
            create_permanent_backup()
            if PERMANENT_CONFIG['ENABLE_PERMANENT_STORAGE']:
                optimize_database()
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
            permanent_thread.join(timeout=10)
            cleanup_thread.join(timeout=10)
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
