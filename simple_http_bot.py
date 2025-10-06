import os
import re
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# 机器人配置
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'
TELEGRAM_API = f'https://api.telegram.org/bot{BOT_TOKEN}'

def send_message(chat_id, text):
    """发送消息到 Telegram"""
    url = f'{TELEGRAM_API}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': text
    }
    
    try:
        data_encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=data_encoded, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f'发送消息失败: {e}')
        return None

def analyze_phone(phone_text):
    """分析电话号码"""
    digits = re.sub(r'\D', '', phone_text)
    
    result = {
        'number': digits,
        'country': '未知',
        'carrier': '未知',
        'type': '电话号码'
    }
    
    # 中国手机号码判断
    if len(digits) == 11 and digits.startswith('1'):
        result['country'] = '中国'
        result['type'] = '手机号码'
        
        prefix = digits[:3]
        if prefix in ['130', '131', '132', '155', '156', '185', '186']:
            result['carrier'] = '中国联通'
        elif prefix in ['134', '135', '136', '137', '138', '139', '150', '151', '152', '157', '158', '159']:
            result['carrier'] = '中国移动'
        elif prefix in ['133', '153', '180', '181', '189', '177']:
            result['carrier'] = '中国电信'
        else:
            result['carrier'] = '其他运营商'
    
    return result

def is_phone_number(text):
    """检查是否为有效电话号码"""
    digits = re.sub(r'\D', '', text)
    return 7 <= len(digits) <= 15

class TelegramBotHandler(BaseHTTPRequestHandler):
    """处理 HTTP 请求的类"""
    
    def do_POST(self):
        """处理 POST 请求"""
        if self.path == '/webhook':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                # 处理 Telegram 消息
                self.handle_telegram_message(data)
                
                # 返回成功响应
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
                
            except Exception as e:
                print(f'处理消息错误: {e}')
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error'}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_GET(self):
        """处理 GET 请求"""
        if self.path == '/':
            response = {
                'message': '🤖 电话号码查询机器人 v10.3',
                'status': 'running',
                'webhook_endpoint': '/webhook'
            }
        elif self.path == '/health':
            response = {
                'status': 'healthy',
                'service': '电话号码查询机器人'
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
    
    def handle_telegram_message(self, data):
        """处理 Telegram 消息"""
        message = data.get('message')
        if not message:
            return
        
        chat_id = message['chat']['id']
        text = message.get('text', '').strip()
        
        # 处理命令
        if text == '/start':
            response_text = """🤖 电话号码查询机器人 v10.3

📱 功能说明：
• 发送电话号码获取详细信息
• 支持中国手机号码运营商识别
• 支持多种号码格式

💡 使用方法：
直接发送电话号码即可，例如：
• 13800138000
• +86 138 0013 8000

🆘 获取帮助：/help

开始发送号码进行查询吧！"""
            
        elif text == '/help':
            response_text = """🆘 帮助信息

📱 支持的号码格式：
• 13800138000
• +86 138 0013 8000
• 138-0013-8000

✅ 功能特点：
• 中国手机号码识别
• 运营商信息查询
• 号码格式验证

📋 可用命令：
/start - 开始使用
/help - 显示帮助

💡 使用提示：
直接发送电话号码即可获取详细信息！"""
            
        else:
            # 处理电话号码查询
            if is_phone_number(text):
                phone_info = analyze_phone(text)
                
                response_text = f"""📱 电话号码信息查询结果

🔢 号码：{phone_info['number']}
🏳️ 国家/地区：{phone_info['country']}
🏢 运营商：{phone_info['carrier']}
📶 类型：{phone_info['type']}

✅ 查询成功！

继续发送其他号码进行查询。"""
            else:
                response_text = """❌ 这不是一个有效的电话号码

📋 请发送正确格式的电话号码：
• 13800138000
• +86 138 0013 8000

或发送 /help 查看详细使用说明。"""
        
        # 发送回复
        send_message(chat_id, response_text)
    
    def log_message(self, format, *args):
        """禁用默认日志输出"""
        pass

def run_server():
    """启动 HTTP 服务器"""
    port = int(os.environ.get('PORT', 5000))
    server_address = ('', port)
    
    httpd = HTTPServer(server_address, TelegramBotHandler)
    print(f'🚀 电话号码查询机器人启动成功！')
    print(f'📡 监听端口: {port}')
    print(f'🌐 Webhook 地址: /webhook')
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n⚠️ 服务器停止')
        httpd.server_close()

if __name__ == '__main__':
    run_server()
