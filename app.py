import json
import os
import time
import secrets
import logging
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_default_data_dir = '/app/data' if os.path.exists('/app/data') else str(Path(__file__).parent / 'data')
DATA_DIR = Path(os.environ.get('DATA_DIR', _default_data_dir))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CARDS_FILE = DATA_DIR / 'cards.json'
LOCK_FILE = DATA_DIR / 'cards.json.lock'
VALID_BUCKETS = ['ideas', 'in-progress', 'shared-progress', 'complete']

_lock = FileLock(str(LOCK_FILE), timeout=5)


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


@app.route('/')
def index():
    with _lock:
        data = _load_cards()
    buckets = {b: [] for b in VALID_BUCKETS}
    for card in data['cards']:
        b = card.get('bucket', 'ideas')
        if b in buckets:
            buckets[b].append(card)
    buckets['ideas'].sort(key=lambda c: c.get('priority', 999))
    return render_template('index.html', buckets=buckets, cards_json=json.dumps(data['cards']))


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/api/cards', methods=['GET'])
def get_cards():
    with _lock:
        data = _load_cards()
    return jsonify(data['cards'])


@app.route('/api/cards', methods=['POST'])
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
            'created_at': _now(),
            'updated_at': _now(),
        }
        data['cards'].append(card)
        if bucket == 'ideas':
            _renumber_ideas(data['cards'])
            card['priority'] = 0
            for c in data['cards']:
                if c['bucket'] == 'ideas' and c['id'] != card['id']:
                    c['priority'] += 1
            _renumber_ideas(data['cards'])
        _save_cards(data)
    return jsonify({'status': 'ok', 'card': card}), 201


@app.route('/api/cards/quick', methods=['POST'])
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
def get_card(card_id):
    with _lock:
        data = _load_cards()
    card = next((c for c in data['cards'] if c['id'] == card_id), None)
    if not card:
        return jsonify({'error': 'not found'}), 404
    return jsonify(card)


@app.route('/api/cards/<card_id>', methods=['PUT'])
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
