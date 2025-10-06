import os
import re
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# 机器人配置
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'
TELEGRAM_API = f'https://api.telegram.org/bot{BOT_TOKEN}'

def send_message(chat_id, text):
    """发送消息到 Telegram"""
    url = f'{TELEGRAM_API}/sendMessage'
    data = {'chat_id': chat_id, 'text': text}
    try:
        response = requests.post(url, json=data)
        return response.json()
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

@app.route('/webhook', methods=['POST'])
def webhook():
    """处理 Telegram webhook"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'status': 'no data'}), 400
            
        message = data.get('message')
        if not message:
            return jsonify({'status': 'no message'})
        
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
• (138) 0013-8000

✅ 功能特点：
• 中国手机号码识别
• 运营商信息查询
• 号码格式验证
• 地区信息判断

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

继续发送其他号码进行查询，或发送 /help 获取帮助。"""
            else:
                response_text = """❌ 这不是一个有效的电话号码

📋 请发送正确格式的电话号码：
• 13800138000
• +86 138 0013 8000
• 138-0013-8000

或发送 /help 查看详细使用说明。"""
        
        # 发送回复
        send_message(chat_id, response_text)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        print(f'Webhook 错误: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health')
def health():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': '电话号码查询机器人',
        'version': 'v10.3'
    })

@app.route('/')
def home():
    """主页"""
    return jsonify({
        'message': '🤖 电话号码查询机器人 v10.3',
        'status': 'running',
        'webhook_endpoint': '/webhook',
        'health_check': '/health'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'启动机器人服务，端口: {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
