mport os
import re
import json
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

# 配置
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'

# 初始化 Flask 应用
app = Flask(__name__)

# 初始化机器人和调度器
bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

# 简单的全局状态
stats = {
    'queries': 0,
    'successful_queries': 0,
    'failed_queries': 0,
    'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
}

user_stats = {}
user_levels = {}

def is_phone_number(text):
    """检测是否为电话号码"""
    # 移除所有非数字字符
    digits_only = re.sub(r'\D', '', text)
    
    # 检查长度和格式
    if 7 <= len(digits_only) <= 15:
        return True
    
    # 常见格式检查
    patterns = [
        r'^\+?\d{1,4}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}$',
        r'^\d{3}-\d{3}-\d{4}$',
        r'^\+\d{1,15}$'
    ]
    
    for pattern in patterns:
        if re.match(pattern, text.strip()):
            return True
    
    return False

def analyze_phone(phone_text):
    """分析电话号码"""
    digits_only = re.sub(r'\D', '', phone_text)
    
    result = {
        'number': digits_only,
        'formatted': phone_text,
        'country': '未知',
        'carrier': '未知',
        'type': '移动电话'
    }
    
    # 中国手机号判断
    if len(digits_only) == 11 and digits_only.startswith('1'):
        result['country'] = '中国'
        prefix = digits_only[:3]
        
        # 运营商判断
        if prefix in ['130', '131', '132', '155', '156', '185', '186']:
            result['carrier'] = '中国联通'
        elif prefix in ['134', '135', '136', '137', '138', '139', '150', '151', '152']:
            result['carrier'] = '中国移动'
        elif prefix in ['133', '153', '180', '181', '189']:
            result['carrier'] = '中国电信'
        else:
            result['carrier'] = '其他运营商'
    
    # 固话判断
    elif len(digits_only) >= 7 and len(digits_only) <= 11:
        result['type'] = '固定电话'
        if digits_only.startswith('010'):
            result['country'] = '中国'
            result['carrier'] = '北京固话'
        elif digits_only.startswith('021'):
            result['country'] = '中国'
            result['carrier'] = '上海固话'
    
    return result

def update_user_stats(user_id, success=True):
    """更新用户统计"""
    global stats, user_stats, user_levels
    
    # 全局统计
    stats['queries'] += 1
    if success:
        stats['successful_queries'] += 1
    else:
        stats['failed_queries'] += 1
    
    # 用户统计
    if user_id not in user_stats:
        user_stats[user_id] = {'queries': 0, 'successful': 0}
    
    user_stats[user_id]['queries'] += 1
    if success:
        user_stats[user_id]['successful'] += 1
    
    # 用户等级
    if user_id not in user_levels:
        user_levels[user_id] = {'level': 1, 'exp': 0}
    
    user_levels[user_id]['exp'] += 10 if success else 5
    
    # 升级
    level_info = user_levels[user_id]
    if level_info['exp'] >= level_info['level'] * 100:
        level_info['level'] += 1
        level_info['exp'] = 0

def start_command(update, context):
    """开始命令"""
    message = """🤖 电话号码查询机器人

📱 发送电话号码获取详细信息
🔍 支持手机号码和固定电话
⭐ 用户等级积分系统

命令列表：
/help - 帮助
/stats - 系统统计  
/mystats - 个人统计

直接发送号码开始查询！"""
    
    update.message.reply_text(message)

def help_command(update, context):
    """帮助命令"""
    help_text = """🆘 使用帮助

直接发送电话号码即可查询：
• 13800138000
• +86 138 0013 8000
• 010-12345678

支持格式：
✅ 中国手机号
✅ 固定电话
✅ 国际号码

/start - 开始
/stats - 系统统计
/mystats - 个人统计"""
    
    update.message.reply_text(help_text)

def stats_command(update, context):
    """统计命令"""
    success_rate = 0
    if stats['queries'] > 0:
        success_rate = (stats['successful_queries'] / stats['queries']) * 100
    
    message = f"""📊 系统统计

🔍 总查询: {stats['queries']}
✅ 成功: {stats['successful_queries']}
❌ 失败: {stats['failed_queries']}
📈 成功率: {success_rate:.1f}%
👥 用户数: {len(user_stats)}
🕒 启动时间: {stats['start_time']}

🤖 版本: v10.3"""
    
    update.message.reply_text(message)

def mystats_command(update, context):
    """个人统计命令"""
    user_id = update.effective_user.id
    
    if user_id not in user_stats:
        update.message.reply_text("还没有查询记录，发送号码开始吧！")
        return
    
    user_data = user_stats[user_id]
    level_data = user_levels.get(user_id, {'level': 1, 'exp': 0})
    
    success_rate = 0
    if user_data['queries'] > 0:
        success_rate = (user_data['successful'] / user_data['queries']) * 100
    
    message = f"""👤 个人统计

⭐ 等级: {level_data['level']}
💎 经验: {level_data['exp']}/100
🔍 查询: {user_data['queries']}
✅ 成功: {user_data['successful']}
📈 成功率: {success_rate:.1f}%

继续查询获得经验值！"""
    
    update.message.reply_text(message)

def handle_message(update, context):
    """处理消息"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if not is_phone_number(text):
        update.message.reply_text("❌ 请发送有效的电话号码")
        update_user_stats(user_id, success=False)
        return
    
    try:
        info = analyze_phone(text)
        
        response = f"""📱 号码信息

🔢 号码: {info['formatted']}
🏳️ 国家: {info['country']}
🏢 运营商: {info['carrier']}
📶 类型: {info['type']}

✅ 查询成功！"""
        
        update.message.reply_text(response)
        update_user_stats(user_id, success=True)
        
    except Exception as e:
        update.message.reply_text(f"❌ 查询失败: {str(e)}")
        update_user_stats(user_id, success=False)

# 注册处理器
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("help", help_command))
dispatcher.add_handler(CommandHandler("stats", stats_command))
dispatcher.add_handler(CommandHandler("mystats", mystats_command))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

@app.route('/webhook', methods=['POST'])
def webhook():
    """处理webhook"""
    try:
        json_data = request.get_json()
        update = Update.de_json(json_data, bot)
        dispatcher.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        print(f"错误: {e}")
        return jsonify({'status': 'error'}), 500

@app.route('/status')
def status():
    """状态检查"""
    return jsonify({
        'status': 'running',
        'stats': stats,
        'bot_username': bot.get_me().username
    })

@app.route('/')
def home():
    """主页"""
    return jsonify({
        'message': '电话号码查询机器人 v10.3',
        'status': 'running'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
