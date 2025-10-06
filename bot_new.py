import os
import re
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

app = Flask(__name__)
bot = Bot(token='8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
dispatcher = Dispatcher(bot, None, workers=0)

def start(update, context):
    msg = """🤖 电话号码查询机器人

📱 直接发送电话号码获取信息
🔍 支持中国手机号码识别

命令:
/help - 帮助信息

发送号码开始查询!"""
    update.message.reply_text(msg)

def help_cmd(update, context):
    msg = """📋 使用说明

直接发送电话号码即可查询:
• 13800138000
• +86 138 0013 8000

支持功能:
✅ 中国手机号码
✅ 运营商识别
✅ 号码格式化

示例: 发送 13800138000"""
    update.message.reply_text(msg)

def handle_text(update, context):
    text = update.message.text.strip()
    digits = re.sub(r'\D', '', text)
    
    if len(digits) >= 7 and len(digits) <= 15:
        # 中国手机号码判断
        if len(digits) == 11 and digits.startswith('1'):
            carrier = '未知运营商'
            prefix = digits[:3]
            
            if prefix in ['130', '131', '132', '155', '156', '185', '186']:
                carrier = '中国联通'
            elif prefix in ['134', '135', '136', '137', '138', '139', '150', '151', '152', '157', '158', '159']:
                carrier = '中国移动'
            elif prefix in ['133', '153', '180', '181', '189', '177']:
                carrier = '中国电信'
            
            result = f"""📱 号码信息

🔢 号码: {digits}
🏳️ 国家: 中国
🏢 运营商: {carrier}
📶 类型: 手机号码

✅ 查询成功!"""
        
        else:
            result = f"""📞 号码信息

🔢 号码: {digits}
📶 类型: 电话号码
🌍 说明: 非中国手机号

✅ 查询完成!"""
        
        update.message.reply_text(result)
    else:
        update.message.reply_text('❌ 请发送有效的电话号码\n\n示例格式:\n• 13800138000\n• +86 138 0013 8000')

# 注册处理器
dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(CommandHandler('help', help_cmd))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if data:
            update = Update.de_json(data, bot)
            dispatcher.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        print(f'Webhook error: {e}')
        return jsonify({'status': 'error'}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'bot': 'running'})

@app.route('/')
def home():
    return jsonify({
        'message': '电话号码查询机器人 v10.3',
        'status': 'running',
        'webhook': '/webhook'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
