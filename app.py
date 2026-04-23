import json
import os
import time
import secrets
import logging
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from filelock import FileLock
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, flash
from ldap3 import Server, Connection, ALL, SIMPLE, SUBTREE, ALL_ATTRIBUTES
from ldap3.core.exceptions import LDAPException

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_default_data_dir = '/app/data' if os.path.exists('/app/data') else str(Path(__file__).parent / 'data')
DATA_DIR = Path(os.environ.get('DATA_DIR', _default_data_dir))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CARDS_FILE = DATA_DIR / 'cards.json'
LOCK_FILE = DATA_DIR / 'cards.json.lock'
ALLOWED_USERS_FILE = DATA_DIR / 'allowed_users.json'
VALID_BUCKETS = ['ideas', 'in-progress', 'shared-progress', 'complete']

LDAP_SERVER = 'ldap://ds.cisco.com:389'
LDAP_BASE_DN = 'OU=Employees,OU=Cisco Users,DC=cisco,DC=com'

_lock = FileLock(str(LOCK_FILE), timeout=5)


# ── Allowed users whitelist ──────────────────────────────────────────────

def _load_allowed_users():
    if not ALLOWED_USERS_FILE.exists():
        default = {"allowed_cec_ids": ["kamancha"], "admins": ["kamancha"]}
        with open(ALLOWED_USERS_FILE, 'w') as f:
            json.dump(default, f, indent=2)
        return default
    with open(ALLOWED_USERS_FILE) as f:
        return json.load(f)


def _is_allowed(cec_id):
    data = _load_allowed_users()
    allowed = [c.lower() for c in data.get('allowed_cec_ids', [])]
    return cec_id.lower() in allowed


# ── LDAP authentication ──────────────────────────────────────────────────

def authenticate_ldap_user(cec_id, password):
    user_dn = f'cn={cec_id},OU=Employees,OU=Cisco Users,DC=cisco,DC=com'
    try:
        server = Server(LDAP_SERVER, get_info=ALL, connect_timeout=5)
        conn = Connection(server, user=user_dn, password=password,
                          authentication=SIMPLE, auto_bind=True, raise_exceptions=True)
        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=f'(distinguishedName={user_dn})',
            search_scope=SUBTREE,
            attributes=ALL_ATTRIBUTES
        )
        conn.unbind()
        return True, None
    except LDAPException as e:
        logger.warning("LDAP auth failed for %s: %s", cec_id, e)
        return False, str(e)
    except Exception as e:
        logger.error("LDAP unexpected error for %s: %s", cec_id, e)
        return False, 'LDAP server unreachable'


# ── Auth decorator ───────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('cec_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'unauthorized'}), 401
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ──────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('cec_id'):
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        cec_id = (request.form.get('cec_id') or '').strip().lower()
        password = request.form.get('password') or ''

        if not cec_id or not password:
            error = 'CEC-ID and password are required.'
        elif not _is_allowed(cec_id):
            error = f'Access denied. Contact kamancha to request access for "{cec_id}".'
            logger.warning("Login denied — not in whitelist: %s", cec_id)
        else:
            ok, err = authenticate_ldap_user(cec_id, password)
            if ok:
                session['cec_id'] = cec_id
                logger.info("Login success: %s", cec_id)
                next_url = request.args.get('next') or url_for('index')
                return redirect(next_url)
            else:
                error = 'Invalid CEC credentials. Please check your password.'
                logger.warning("Login failed for %s: %s", cec_id, err)

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    cec_id = session.pop('cec_id', None)
    if cec_id:
        logger.info("Logout: %s", cec_id)
    return redirect(url_for('login'))


# ── Card helpers ─────────────────────────────────────────────────────────

def _generate_id():
    return f"card_{int(time.time())}_{secrets.token_hex(3)}"


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_cards():
    if not CARDS_FILE.exists():
        return {"meta": {"version": 1, "last_modified": _now()}, "cards": []}
    with open(CARDS_FILE) as f:
        return json.load(f)


def _save_cards(data):
    data['meta']['last_modified'] = _now()
    with open(CARDS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _renumber_ideas(cards):
    ideas = sorted([c for c in cards if c['bucket'] == 'ideas'], key=lambda c: c.get('priority', 999))
    for i, card in enumerate(ideas):
        card['priority'] = i


# ── Board route ──────────────────────────────────────────────────────────

def _migrate_cards(data):
    changed = False
    for card in data['cards']:
        if 'created_by' not in card:
            card['created_by'] = 'kamancha'
            changed = True
    if changed:
        _save_cards(data)
    return data


@app.route('/')
@login_required
def index():
    with _lock:
        data = _load_cards()
        data = _migrate_cards(data)
    buckets = {b: [] for b in VALID_BUCKETS}
    for card in data['cards']:
        b = card.get('bucket', 'ideas')
        if b in buckets:
            buckets[b].append(card)
    buckets['ideas'].sort(key=lambda c: c.get('priority', 999))
    return render_template('index.html', buckets=buckets,
                           cards_json=json.dumps(data['cards']),
                           current_user=session['cec_id'])


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


# ── Card API routes (all protected) ─────────────────────────────────────

@app.route('/api/cards', methods=['GET'])
@login_required
def get_cards():
    with _lock:
        data = _load_cards()
    return jsonify(data['cards'])


@app.route('/api/cards', methods=['POST'])
@login_required
def create_card():
    body = request.get_json(force=True) or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400
    bucket = body.get('bucket', 'ideas')
    if bucket not in VALID_BUCKETS:
        return jsonify({'error': f'invalid bucket: {bucket}'}), 400

    with _lock:
        data = _load_cards()
        card = {
            'id': _generate_id(),
            'title': title[:200],
            'description': (body.get('description') or '').strip(),
            'bucket': bucket,
            'priority': 999,
            'cec_ids': [],
            'created_by': session.get('cec_id', 'unknown'),
            'created_at': _now(),
            'updated_at': _now(),
        }
        data['cards'].append(card)
        if bucket == 'ideas':
            for c in data['cards']:
                if c['bucket'] == 'ideas' and c['id'] != card['id']:
                    c['priority'] += 1
            card['priority'] = 0
            _renumber_ideas(data['cards'])
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card}), 201


@app.route('/api/cards/quick', methods=['POST'])
@login_required
def quick_add():
    body = request.get_json(force=True) or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400

    with _lock:
        data = _load_cards()
        card = {
            'id': _generate_id(),
            'title': title[:200],
            'description': (body.get('description') or '').strip(),
            'bucket': 'ideas',
            'priority': 0,
            'cec_ids': [],
            'created_by': session.get('cec_id', 'unknown'),
            'created_at': _now(),
            'updated_at': _now(),
        }
        for c in data['cards']:
            if c['bucket'] == 'ideas':
                c['priority'] += 1
        data['cards'].append(card)
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card}), 201


@app.route('/api/cards/reorder-bulk', methods=['POST'])
@login_required
def reorder_bulk():
    body = request.get_json(force=True) or {}
    card_ids = body.get('card_ids', [])
    if not isinstance(card_ids, list):
        return jsonify({'error': 'card_ids must be a list'}), 400

    with _lock:
        data = _load_cards()
        id_to_priority = {cid: i for i, cid in enumerate(card_ids)}
        for card in data['cards']:
            if card['id'] in id_to_priority:
                card['priority'] = id_to_priority[card['id']]
                card['updated_at'] = _now()
        _save_cards(data)
    return jsonify({'status': 'ok'})


@app.route('/api/cards/<card_id>', methods=['GET'])
@login_required
def get_card(card_id):
    with _lock:
        data = _load_cards()
    card = next((c for c in data['cards'] if c['id'] == card_id), None)
    if not card:
        return jsonify({'error': 'not found'}), 404
    return jsonify(card)


@app.route('/api/cards/<card_id>', methods=['PUT'])
@login_required
def update_card(card_id):
    body = request.get_json(force=True) or {}
    with _lock:
        data = _load_cards()
        card = next((c for c in data['cards'] if c['id'] == card_id), None)
        if not card:
            return jsonify({'error': 'not found'}), 404
        if 'title' in body:
            title = (body['title'] or '').strip()
            if not title:
                return jsonify({'error': 'title cannot be empty'}), 400
            card['title'] = title[:200]
        if 'description' in body:
            card['description'] = (body['description'] or '').strip()
        card['updated_at'] = _now()
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card})


@app.route('/api/cards/<card_id>', methods=['DELETE'])
@login_required
def delete_card(card_id):
    with _lock:
        data = _load_cards()
        idx = next((i for i, c in enumerate(data['cards']) if c['id'] == card_id), None)
        if idx is None:
            return jsonify({'error': 'not found'}), 404
        bucket = data['cards'][idx]['bucket']
        data['cards'].pop(idx)
        if bucket == 'ideas':
            _renumber_ideas(data['cards'])
        _save_cards(data)
    return jsonify({'status': 'ok'})


@app.route('/api/cards/<card_id>/move', methods=['PUT'])
@login_required
def move_card(card_id):
    body = request.get_json(force=True) or {}
    new_bucket = body.get('bucket', '')
    if new_bucket not in VALID_BUCKETS:
        return jsonify({'error': f'invalid bucket: {new_bucket}'}), 400

    with _lock:
        data = _load_cards()
        card = next((c for c in data['cards'] if c['id'] == card_id), None)
        if not card:
            return jsonify({'error': 'not found'}), 404
        old_bucket = card['bucket']
        card['bucket'] = new_bucket
        card['updated_at'] = _now()
        if old_bucket != new_bucket:
            if new_bucket != 'shared-progress':
                card['cec_ids'] = []
            if new_bucket == 'ideas':
                card['priority'] = max((c.get('priority', 0) for c in data['cards'] if c['bucket'] == 'ideas'), default=-1) + 1
            else:
                card['priority'] = 999
            if old_bucket == 'ideas':
                _renumber_ideas(data['cards'])
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card})


@app.route('/api/cards/<card_id>/cec', methods=['POST'])
@login_required
def add_cec(card_id):
    body = request.get_json(force=True) or {}
    cec_id = (body.get('cec_id') or '').strip().lower()
    if not cec_id:
        return jsonify({'error': 'cec_id is required'}), 400

    with _lock:
        data = _load_cards()
        card = next((c for c in data['cards'] if c['id'] == card_id), None)
        if not card:
            return jsonify({'error': 'not found'}), 404
        if card['bucket'] != 'shared-progress':
            return jsonify({'error': 'card must be in shared-progress bucket'}), 400
        if cec_id not in card['cec_ids']:
            card['cec_ids'].append(cec_id)
            card['updated_at'] = _now()
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card})


@app.route('/api/cards/<card_id>/cec/<cec_id>', methods=['DELETE'])
@login_required
def remove_cec(card_id, cec_id):
    with _lock:
        data = _load_cards()
        card = next((c for c in data['cards'] if c['id'] == card_id), None)
        if not card:
            return jsonify({'error': 'not found'}), 404
        cec_id = cec_id.lower()
        if cec_id in card['cec_ids']:
            card['cec_ids'].remove(cec_id)
            card['updated_at'] = _now()
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 9999))
    app.run(host='0.0.0.0', port=port, debug=False)
