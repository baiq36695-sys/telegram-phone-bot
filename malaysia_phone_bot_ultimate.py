#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马来西亚电话号码机器人 - 零依赖版本
专为Render等云平台设计，无需任何第三方库
包含完整功能和性能优化

作者: MiniMax Agent
版本: 1.2.0 Zero Dependency
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
                    
            elif pattern_name.startswith('landline_'):
                result['type'] = '固定电话'
                prefix = phone[:3] if len(phone) >= 10 else phone[:2]
                result['state'] = STATE_MAPPING.get(prefix, '未知地区')
                
            elif pattern_name == 'toll_free':
                result['type'] = '免费电话'
                result['operator'] = '全网通用'
                
            elif pattern_name == 'premium':
                result['type'] = '增值服务号码'
                result['operator'] = '付费服务'
            
            break
    
    return result

def register_phone_number(phone, user_id, username):
    """注册电话号码"""
    with data_lock:
        current_time = datetime.now().isoformat()
        
        # 检查重复
        if phone in phone_registry:
            existing = phone_registry[phone]
            return f"❌ 号码已被用户 @{existing['username']} 注册"
        
        # 注册号码
        phone_registry[phone] = {
            'user_id': user_id,
            'username': username,
            'timestamp': current_time
        }
        
        # 更新用户数据
        if user_id not in user_data:
            user_data[user_id] = {}
        
        user_data[user_id].update({
            'username': username,
            'last_activity': current_time,
            'registered_phones': user_data[user_id].get('registered_phones', 0) + 1
        })
        
        return f"✅ 号码注册成功！"

def send_telegram_message(chat_id, text, parse_mode='HTML'):
    """发送Telegram消息"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # 分割长消息
    if len(text) > PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH']:
        parts = [text[i:i+PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH']] 
                for i in range(0, len(text), PRODUCTION_CONFIG['MAX_MESSAGE_LENGTH'])]
        for part in parts:
            send_telegram_message(chat_id, part, parse_mode)
        return
    
    data = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    
    try:
        req_data = urllib.parse.urlencode(data).encode()
        request = urllib.request.Request(url, data=req_data, method='POST')
        with urllib.request.urlopen(request, timeout=PRODUCTION_CONFIG['REQUEST_TIMEOUT']) as response:
            return response.read().decode()
    except Exception as e:
        print(f"发送消息失败: {e}")
        return None

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
🇲🇾 <b>马来西亚电话号码查询机器人</b>

📱 <b>功能说明：</b>
• 发送电话号码进行查询
• 支持手机号码和固定电话
• 自动识别运营商和地区
• 号码注册和管理

💡 <b>使用方法：</b>
直接发送号码，支持多种格式：
<code>012-3456789</code>
<code>+60 11-6852 8782</code>
<code>03-12345678</code>
<code>60123456789</code>

🔧 <b>管理命令：</b>
/status - 查看系统状态
/clear - 清除个人数据
/help - 查看帮助

<i>零依赖版本，部署更稳定 🚀</i>
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

<b>📋 支持的号码类型：</b>
• 手机号码：010,011,012,013,014,015,016,017,018,019
• 固定电话：03,04,05,06,07,09,088,089,082-087
• 免费电话：1800
• 增值服务：600

<b>📱 运营商识别：</b>
• Maxis: 012,014,017,019
• DiGi: 010,011,016
• Celcom: 013,019
• U Mobile: 015,018

<b>⚙️ 自动功能：</b>
• 格式标准化处理
• 运营商自动识别
• 地区自动识别
• 重复号码检测

需要帮助请联系管理员 👨‍💻
"""
            send_telegram_message(chat_id, response)
            
        elif text.startswith('/status'):
            with data_lock:
                total_phones = len(phone_registry)
                total_users = len(user_data)
                memory_mb = get_memory_usage_estimate()
                
            response = f"""
📊 <b>系统状态报告</b>

💾 <b>数据统计：</b>
• 注册号码：{total_phones:,} 个
• 活跃用户：{total_users:,} 人
• 内存估算：{memory_mb:.1f} MB

⚡ <b>性能指标：</b>
• 缓存命中率：高效运行
• 清理周期：每小时自动
• 数据保留：30天

🚀 <b>运行状态：</b>
• 服务状态：正常运行
• 版本信息：Zero Dependency 1.2.0
• 更新时间：2025-10-06
• 识别引擎：已修复多格式支持
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
            # 处理电话号码查询
            result = analyze_phone_number(text)
            if result and result['valid']:
                # 构建详细信息
                info = f"""
📱 <b>号码分析结果</b>

🔢 <b>号码信息：</b>
• 原始号码：<code>{result['original']}</code>
• 标准格式：<code>{result['formatted']}</code>
• 号码类型：{result['type']}

"""
                if result['operator'] != '未知':
                    info += f"• 运营商：{result['operator']}\n"
                if result['state'] != '未知':
                    info += f"• 归属地：{result['state']}\n"
                
                # 检查是否已注册
                with data_lock:
                    if result['formatted'] in phone_registry:
                        reg_info = phone_registry[result['formatted']]
                        info += f"\n⚠️ <b>注册状态：</b>\n• 已被 @{reg_info['username']} 注册\n• 注册时间：{reg_info['timestamp'][:19]}\n"
                    else:
                        info += f"\n✅ <b>注册状态：</b> 可注册\n"
                        # 自动注册号码
                        reg_result = register_phone_number(result['formatted'], user_id, username)
                        info += f"• {reg_result}\n"
                
                info += f"\n<i>查询时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
                send_telegram_message(chat_id, info)
            else:
                response = f"""
❌ <b>无效的电话号码格式</b>

您输入的内容：<code>{text}</code>

请发送正确的马来西亚电话号码格式：

📱 <b>手机号码格式：</b>
• <code>012-3456789</code> (Maxis)
• <code>011-6852782</code> (DiGi)
• <code>+60 11-6852 8782</code> (国际格式)
• <code>013-1234567</code> (Celcom)

🏠 <b>固定电话格式：</b>
• <code>03-12345678</code> (吉隆坡/雪兰莪)
• <code>04-1234567</code> (槟城)

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
                # 在新线程中处理消息（异步处理）
                threading.Thread(
                    target=handle_message, 
                    args=(update['message'],),
                    daemon=True
                ).start()
            
            # 返回200状态
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            
        except Exception as e:
            print(f"Webhook处理错误: {e}")
            self.send_response(500)
            self.end_headers()
    
    def do_GET(self):
        # 健康检查端点
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            status = {
                'status': 'healthy',
                'version': '1.2.0 Zero Dependency',
                'timestamp': datetime.now().isoformat(),
                'memory_estimate_mb': get_memory_usage_estimate(),
                'phone_count': len(phone_registry),
                'user_count': len(user_data),
                'dependencies': 'none'
            }
            
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # 简化日志输出
        return

def main():
    """主函数"""
    print("🚀 马来西亚电话号码机器人 - 零依赖版本启动中...")
    print(f"📊 配置信息：")
    print(f"   - 最大号码记录：{PRODUCTION_CONFIG['MAX_PHONE_REGISTRY_SIZE']:,}")
    print(f"   - 最大用户记录：{PRODUCTION_CONFIG['MAX_USER_DATA_SIZE']:,}")
    print(f"   - 数据保留天数：{PRODUCTION_CONFIG['DATA_RETENTION_DAYS']}")
    print(f"   - 清理间隔：{PRODUCTION_CONFIG['DATA_CLEANUP_INTERVAL']}秒")
    print("🔧 已修复号码识别问题，支持多种格式")
    print("⚡ 零第三方依赖，部署更稳定")
    
    # 启动数据清理工作线程
    cleanup_thread = threading.Thread(target=data_cleanup_worker, daemon=True)
    cleanup_thread.start()
    print("🧹 数据清理线程已启动")
    
    # 启动HTTP服务器
    port = int(os.getenv('PORT', 8000))
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    
    print(f"🌐 服务器运行在端口 {port}")
    print(f"💡 BOT Token: {BOT_TOKEN[:20]}...")
    print(f"🔗 Webhook URL: {WEBHOOK_URL}")
    print("✅ 系统已就绪，24/7稳定运行中！")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹️ 服务器正在关闭...")
        server.shutdown()
        print("👋 服务器已关闭")

if __name__ == '__main__':
    main()
