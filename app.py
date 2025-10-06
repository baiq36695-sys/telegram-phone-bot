import os
import re
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

app = Flask(__name__)
bot = Bot(token='8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')
dispatcher = Dispatcher(bot, None, workers=0)

def start(update, context):
    update.message.reply_text('电话号码查询机器人\n\n直接发送号码进行查询')

def help_cmd(update, context):
    update.message.reply_text('发送电话号码获取信息\n\n例如: 13800138000')

def handle_text(update, context):
    text = update.message.text.strip()
    digits = re.sub(r'\D', '', text)
    
    if 7 <= len(digits) <= 15:
        if len(digits) == 11 and digits.startswith('1'):
            msg = f'号码: {digits}\n国家: 中国\n类型: 手机号码'
            if digits[:3] in ['130', '131', '132']:
                msg += '\n运营商: 联通'
            elif digits[:3] in ['134', '135', '136', '137', '138', '139']:
                msg += '\n运营商: 移动'
            elif digits[:3] in ['133', '153', '180', '181', '189']:
                msg += '\n运营商: 电信'
        else:
            msg = f'号码: {digits}\n类型: 电话号码'
        
        update.message.reply_text(msg)
    else:
        update.message.reply_text('请发送有效的电话号码')

dispatcher.add_handler(CommandHandler('start', start))
dispatcher.add_handler(CommandHandler('help', help_cmd))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        update = Update.de_json(data, bot)
        dispatcher.process_update(update)
        return jsonify({'status': 'ok'})
    except:
        return jsonify({'status': 'error'}), 500

@app.route('/')
def home():
    return jsonify({'message': '机器人运行中'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
