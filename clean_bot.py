import os
import re
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

stats = {'queries': 0, 'successful': 0, 'failed': 0}
user_stats = {}
user_levels = {}

def is_phone(text):
    digits = re.sub(r'\D', '', text)
    return 7 <= len(digits) <= 15

def analyze_phone(phone):
    digits = re.sub(r'\D', '', phone)
    result = {'number': digits, 'country': '未知', 'carrier': '未知'}
    
    if len(digits) == 11 and digits.startswith('1'):
        result['country'] = '中国'
        prefix = digits[:3]
        if prefix in ['130', '131', '132', '155', '156']:
            result['carrier'] = '联通'
        elif prefix in ['134', '135', '136', '137', '138', '139']:
            result['carrier'] = '移动'
        elif prefix in ['133', '153', '180', '181', '189']:
            result['carrier'] = '电信'
    
    return result

def update_stats(user_id, success=True):
    stats['queries'] += 1
    if success:
        stats['successful'] += 1
    else:
        stats['failed'] += 1
    
    if user_id not in user_stats:
        user_stats[user_id] = {'queries': 0, 'successful': 0}
        user_levels[user_id] = {'level': 1, 'exp': 0}
    
    user_stats[user_id]['queries'] += 1
    if success:
        user_stats[user_id]['successful'] += 1
        user_levels[user_id]['exp'] += 10

def start_cmd(update, context):
    update.message.reply_text('电话号码查询机器人\n\n直接发送号码查询\n\n/help - 帮助\n/stats - 统计')

def help_cmd(update, context):
    update.message.reply_text('发送电话号码获取信息\n\n支持格式:\n13800138000\n+86 138 0013 8000\n\n/stats - 查看统计')

def stats_cmd(update, context):
    rate = 0
    if stats['queries'] > 0:
        rate = (stats['successful'] / stats['queries']) * 100
    
    msg = f"总查询: {stats['queries']}\n成功: {stats['successful']}\n成功率: {rate:.1f}%"
    update.message.reply_text(msg)

def handle_msg(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if not is_phone(text):
        update.message.reply_text('请发送有效电话号码')
        update_stats(user_id, False)
        return
    
    try:
        info = analyze_phone(text)
        msg = f"号码: {info['number']}\n国家: {info['country']}\n运营商: {info['carrier']}\n\n查询成功!"
        update.message.reply_text(msg)
        update_stats(user_id, True)
    except Exception as e:
        update.message.reply_text(f"查询失败: {str(e)}")
        update_stats(user_id, False)

dispatcher.add_handler(CommandHandler("start", start_cmd))
dispatcher.add_handler(CommandHandler("help", help_cmd))
dispatcher.add_handler(CommandHandler("stats", stats_cmd))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_msg))

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        update = Update.de_json(data, bot)
        dispatcher.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def home():
    return jsonify({'message': '电话号码查询机器人', 'status': 'running'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
