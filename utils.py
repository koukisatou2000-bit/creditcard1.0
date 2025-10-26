import json
import os
from threading import Lock

# ファイル読み書きの排他制御用
config_lock = Lock()
CONFIG_FILE = 'services_config.json'

def load_services_config():
    """サービス設定をJSONファイルから読み込む"""
    with config_lock:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # ファイルが存在しない場合は空の辞書を返す
                return {}
        except Exception as e:
            print(f"設定ファイル読み込みエラー: {e}")
            return {}

def save_services_config(config):
    """サービス設定をJSONファイルに保存"""
    with config_lock:
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"設定ファイル保存エラー: {e}")
            return False

def get_service(service_key):
    """特定のサービス設定を取得"""
    config = load_services_config()
    return config.get(service_key)

def service_exists(service_key):
    """サービスが存在するかチェック"""
    config = load_services_config()
    return service_key in config

def service_is_enabled(service_key):
    """サービスが有効化されているかチェック"""
    service = get_service(service_key)
    if service:
        return service.get('enabled', False)
    return False

def get_all_services():
    """全サービスのリストを取得"""
    return load_services_config()

def update_service(service_key, service_data):
    """サービス設定を更新"""
    config = load_services_config()
    config[service_key] = service_data
    return save_services_config(config)

def delete_service(service_key):
    """サービスを削除"""
    config = load_services_config()
    if service_key in config:
        del config[service_key]
        return save_services_config(config)
    return False

def toggle_service(service_key):
    """サービスの有効/無効を切り替え"""
    config = load_services_config()
    if service_key in config:
        config[service_key]['enabled'] = not config[service_key].get('enabled', False)
        return save_services_config(config)
    return False

def create_new_service():
    """新規サービスを作成（noname1, noname2...）"""
    config = load_services_config()
    
    # 既存のnonameサービスの番号を取得
    existing_numbers = []
    for key in config.keys():
        if key.startswith('noname'):
            try:
                num = int(key.replace('noname', ''))
                existing_numbers.append(num)
            except:
                pass
    
    # 次の番号を決定
    next_num = 1
    if existing_numbers:
        next_num = max(existing_numbers) + 1
    
    new_key = f'noname{next_num}'
    
    # デフォルト設定
    config[new_key] = {
        "name": f"新規サービス{next_num}",
        "enabled": False,
        "display_name": f"新規サービス{next_num}決済システム",
        "title": "お支払い情報の入力",
        "description": "カード情報を入力してください。",
        "logo_url": "",
        "custom_link": f"service{next_num}",
        "design": {
            "primary_color": "#000000",
            "secondary_color": "#ffffff",
            "background_color": "#ffffff",
            "text_color": "#000000",
            "button_bg_color": "#000000",
            "button_text_color": "#ffffff",
            "border_color": "#000000",
            "font_family": "Arial, sans-serif",
            "custom_css": ""
        }
    }
    
    if save_services_config(config):
        return new_key
    return None

def check_duplicate_name(service_key, name):
    """同じnameを持つサービスが他に存在するかチェック"""
    config = load_services_config()
    for key, service in config.items():
        if key != service_key and service.get('name') == name:
            return True
    return False