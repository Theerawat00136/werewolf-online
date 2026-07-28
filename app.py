import os  # 🟢 เพิ่ม import os ตรงนี้
import random
import string
from flask import Flask, render_template, request
from extensions import db, socketio
import models
from models import GameRoom, User, PlayerStatus
from flask_socketio import emit, join_room

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'werewolf_super_secret_key'
    
    # 🟢 ลอจิกดึงลิงก์ฐานข้อมูลจากเซิร์ฟเวอร์ ถ้าหาไม่เจอจะกลับไปใช้ของเครื่องเราแทน
    db_url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://postgres:pass1234@localhost:5432/werewolf_db')
    
    # 🟢 เซิร์ฟเวอร์ Render มักจะให้ลิงก์ที่ขึ้นต้นด้วย postgres:// ซึ่งต้องแก้เป็น postgresql:// 
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    socketio.init_app(app)
    with app.app_context():
        db.create_all()
    return app

app = create_app()

night_actions = {}
day_votes = {}
room_extras = {} 

def generate_room_code(length=5):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('create_room')
def handle_create_room(data):
    username = data.get('username')
    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username)
        db.session.add(user)
        db.session.commit()

    room_code = generate_room_code()
    new_room = GameRoom(room_code=room_code, host_id=user.id)
    db.session.add(new_room)
    db.session.commit()
    player_status = PlayerStatus(user_id=user.id, room_id=new_room.id)
    db.session.add(player_status)
    db.session.commit()

    join_room(room_code)
    join_room(f"private_{username}")
    emit('room_created', {'room_code': room_code, 'message': f'สร้างห้อง {room_code} สำเร็จ!'})
    emit('player_joined', {'players': [username]}, to=room_code)

@socketio.on('join_room')
def handle_join_room(data):
    username = data.get('username')
    room_code = data.get('room_code')
    room = GameRoom.query.filter_by(room_code=room_code).first()

    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username)
        db.session.add(user)
        db.session.commit()

    existing_player = PlayerStatus.query.filter_by(user_id=user.id, room_id=room.id).first()
    if not existing_player:
        new_player = PlayerStatus(user_id=user.id, room_id=room.id)
        db.session.add(new_player)
        db.session.commit()

    join_room(room_code)
    join_room(f"private_{username}")
    players_in_room = PlayerStatus.query.filter_by(room_id=room.id).all()
    player_names = [User.query.get(p.user_id).username for p in players_in_room]
    emit('join_success', {'room_code': room_code})
    emit('player_joined', {'players': player_names}, to=room_code)

# เก็บข้อมูล Peer ID ของคนในห้อง
room_voices = {}

@socketio.on('register_voice_peer')
def handle_voice_peer(data):
    room_code = data.get('room_code')
    username = data.get('username')
    peer_id = data.get('peer_id')
    
    if not room_code:
        return
        
    if room_code not in room_voices:
        room_voices[room_code] = {}
        
    room_voices[room_code][username] = peer_id
    emit('update_voice_peers', room_voices[room_code], to=room_code)
    
@socketio.on('start_game')
def handle_start_game(data):
    room_code = data.get('room_code')
    roles_dict = data.get('roles')
    room = GameRoom.query.filter_by(room_code=room_code).first()
    players_in_room = PlayerStatus.query.filter_by(room_id=room.id).all()
    
    deck = []
    for role_name, count in roles_dict.items():
        deck.extend([role_name] * count)
    random.shuffle(deck)

    for index, player in enumerate(players_in_room):
        player.role_name = deck[index]
        player.is_alive = True
    
    room.status = 'NIGHT'
    db.session.commit()
    
    room_extras[room_code] = {'lovers': [], 'witch_heal': True, 'witch_poison': True, 'night_count': 1}
    
    emit('game_started', {'message': 'เข้าสู่คืนแรก...'}, to=room_code)
    
    alive_players = [User.query.get(p.user_id).username for p in players_in_room]
    for player in players_in_room:
        user = User.query.get(player.user_id)
        targets = alive_players if player.role_name in ['Bodyguard', 'Cupid'] else [p for p in alive_players if p != user.username]
        emit('receive_role', {
            'role': player.role_name, 
            'targets': targets, 
            'extra_data': room_extras[room_code]
        }, to=f"private_{user.username}")

def check_game_over(room_code):
    room = GameRoom.query.filter_by(room_code=room_code).first()
    alive_players = PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()
    alive_roles = [p.role_name for p in alive_players]
    
    sk_alive = 'Serial Killer' in alive_roles
    total_alive = len(alive_roles)

    # นับจำนวนหมาป่าแท้ๆ (ใช้เช็กกรณีหมาป่าตายหมดเกลี้ยง)
    actual_wolves = sum(1 for r in alive_roles if r in ['Werewolf', 'Alpha Wolf'])
    
    # นับกำลังพลรวมทีมหมาป่า (รวมคนทรยศเข้าไปด้วย เพื่อเอาไว้ตัดสินชัยชนะ)
    wolf_team_total = sum(1 for r in alive_roles if r in ['Werewolf', 'Alpha Wolf', 'Traitor'])

    # เงื่อนไขฆาตกรต่อเนื่องชนะ
    if sk_alive and total_alive <= 2:
        emit('game_over', {'winner': 'Serial Killer'}, to=room_code)
        return True
    
    # เงื่อนไขชาวบ้านชนะ (หมาป่าแท้ตายหมด และฆาตกรตายแล้ว)
    elif actual_wolves == 0 and not sk_alive:
        emit('game_over', {'winner': 'Villagers'}, to=room_code)
        return True
        
    # เงื่อนไขหมาป่าชนะ (ทีมหมาป่ารวมคนทรยศ >= ฝั่งตรงข้าม และฆาตกรตายแล้ว)
    elif wolf_team_total >= (total_alive - wolf_team_total) and not sk_alive:
        emit('game_over', {'winner': 'Werewolves'}, to=room_code)
        return True
        
    return False

def process_deaths(room_code, initial_dead_names):
    room = GameRoom.query.filter_by(room_code=room_code).first()
    lovers = room_extras.get(room_code, {}).get('lovers', [])
    
    dead_set = set(initial_dead_names)
    new_deaths_found = True
    
    while new_deaths_found:
        new_deaths_found = False
        for d in list(dead_set):
            if d in lovers:
                for l in lovers:
                    if l not in dead_set:
                        dead_set.add(l)
                        new_deaths_found = True

    dead_is_hunter = False
    hunter_name = None
    
    for d in dead_set:
        user = User.query.filter_by(username=d).first()
        if user:
            player = PlayerStatus.query.filter_by(user_id=user.id, room_id=room.id).first()
            if player and player.is_alive:
                player.is_alive = False
                if player.role_name == 'Hunter':
                    dead_is_hunter = True
                    hunter_name = d
    
    db.session.commit()
    return list(dead_set), dead_is_hunter, hunter_name

@socketio.on('submit_night_action')
def handle_night_action(data):
    username = data.get('username')
    room_code = data.get('room_code')
    action_data = data.get('action_data')

    if room_code not in night_actions: night_actions[room_code] = {}
    night_actions[room_code][username] = action_data

    room = GameRoom.query.filter_by(room_code=room_code).first()
    players_in_room = PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()

    # 🐺 โค้ดใหม่: ถ้าเป็นหมาป่า ให้ก็อปปี้เป้าหมายไปให้หมาป่าตัวอื่นทันที
    current_user_obj = User.query.filter_by(username=username).first()
    if current_user_obj:
        current_player = next((p for p in players_in_room if p.user_id == current_user_obj.id), None)
        if current_player and current_player.role_name in ['Werewolf', 'Alpha Wolf']:
            for p in players_in_room:
                if p.role_name in ['Werewolf', 'Alpha Wolf']:
                    wolf_username = User.query.get(p.user_id).username
                    night_actions[room_code][wolf_username] = action_data

    # ⏳ โค้ดเดิมที่หายไป: เช็คว่าอาชีพที่ตื่นกลางคืน โหวตครบทุกคนหรือยัง
    night_count = room_extras.get(room_code, {}).get('night_count', 1)
    
    active_roles = ['Werewolf', 'Alpha Wolf', 'Seer', 'Bodyguard', 'Witch', 'Harlot', 'Serial Killer']
    if night_count == 1:
        active_roles.append('Cupid')

    active_players = [p for p in players_in_room if p.role_name in active_roles]
    submitted_count = sum(1 for p in active_players if User.query.get(p.user_id).username in night_actions[room_code])

    # ถ้าทุกคนโหวตครบแล้ว ให้เรียกฟังก์ชันสรุปผลกลางคืน
    if submitted_count == len(active_players) and len(active_players) > 0:
        resolve_night(room_code, players_in_room)

def resolve_night(room_code, players_in_room):
    actions = night_actions.get(room_code, {})
    extras = room_extras.get(room_code, {})
    
    werewolf_target, bodyguard_target, seer_target, seer_username = None, None, None, None
    witch_heal, witch_poison = False, None
    sk_target = None
    harlots = []

    for player in players_in_room:
        user = User.query.get(player.user_id)
        if user.username in actions:
            act = actions[user.username]
            role = player.role_name
            
            if role in ['Werewolf', 'Alpha Wolf']: werewolf_target = act.get('target')
            elif role == 'Serial Killer': sk_target = act.get('target')
            elif role == 'Bodyguard': bodyguard_target = act.get('target')
            elif role == 'Seer': 
                seer_target = act.get('target')
                seer_username = user.username
            elif role == 'Harlot' and act.get('target') != "SKIP":
                harlots.append((user.username, act.get('target')))
            elif role == 'Cupid' and extras.get('night_count') == 1:
                t1, t2 = act.get('target1'), act.get('target2')
                if t1 and t2 and t1 != "SKIP" and t2 != "SKIP":
                    extras['lovers'] = [t1, t2]
            elif role == 'Witch':
                if act.get('heal') and extras.get('witch_heal'):
                    witch_heal = True
                    extras['witch_heal'] = False
                if act.get('poison') and act.get('poison') != "SKIP" and extras.get('witch_poison'):
                    witch_poison = act.get('poison')
                    extras['witch_poison'] = False

    initial_dead = set()
    room = GameRoom.query.filter_by(room_code=room_code).first()
    
    # 1. เช็คหญิงบริการ (Harlot)
    for h_user, h_target in harlots:
        t_user = User.query.filter_by(username=h_target).first()
        t_player = PlayerStatus.query.filter_by(user_id=t_user.id, room_id=room.id).first()
        
        # ถ้าหนีไปบ้านหมาป่า หรือ ฆาตกรต่อเนื่อง
        if t_player and t_player.role_name in ['Werewolf', 'Alpha Wolf', 'Serial Killer']:
            initial_dead.add(h_user) 
        elif h_target == werewolf_target and werewolf_target != bodyguard_target and not witch_heal:
            initial_dead.add(h_user) # ไปบ้านคนที่กำลังจะโดนฆ่า เลยตายคู่
        elif h_target == sk_target and sk_target != bodyguard_target:
            initial_dead.add(h_user) # ไปบ้านคนที่โดนฆาตกรเชือด
            
        if werewolf_target == h_user: werewolf_target = None
        if sk_target == h_user: sk_target = None

    # 2. เช็คหมาป่ากัด และคำสาป
    if werewolf_target and werewolf_target != "SKIP" and werewolf_target != bodyguard_target:
        if not witch_heal:
            target_user = User.query.filter_by(username=werewolf_target).first()
            if target_user:
                target_player = PlayerStatus.query.filter_by(user_id=target_user.id, room_id=room.id).first()
                if target_player.role_name == 'Cursed':
                    target_player.role_name = 'Werewolf'
                    emit('update_role_ui', {'new_role': 'Werewolf'}, to=f"private_{target_user.username}")
                else:
                    initial_dead.add(werewolf_target)
                    
    # 3. เช็คฆาตกรต่อเนื่อง
    if sk_target and sk_target != "SKIP" and sk_target != bodyguard_target:
        initial_dead.add(sk_target)
            
    # 4. ยาพิษแม่มด
    if witch_poison and witch_poison != "SKIP":
        initial_dead.add(witch_poison)

    final_dead_names, dead_is_hunter, hunter_name = process_deaths(room_code, list(initial_dead))
            
    # 5. ผู้หยั่งรู้ส่อง (ลอจิกหลอกตา)
    if seer_target and seer_target != "SKIP" and seer_username:
        target_user = User.query.filter_by(username=seer_target).first()
        if target_user:
            target_player = PlayerStatus.query.filter_by(user_id=target_user.id, room_id=room.id).first()
            seen_role = target_player.role_name
            # ลอจิกหลอกตาของ Alpha Wolf และ Lycan
            if seen_role == 'Alpha Wolf': seen_role = 'Villager'
            elif seen_role == 'Lycan': seen_role = 'Werewolf'
            elif seen_role == 'Traitor': seen_role = 'Villager'
            emit('seer_result', {'target': seer_target, 'role': seen_role}, to=f"private_{seer_username}")
            
    night_actions[room_code] = {} 
    if check_game_over(room_code): return

    room.status = 'DAY'
    db.session.commit()
    
    alive_players = [User.query.get(p.user_id).username for p in PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()]
    dead_msg = ", ".join(final_dead_names) if final_dead_names else None
    
    emit('morning_comes', {
        'dead_player': dead_msg, 
        'alive_players': alive_players, 
        'dead_is_hunter': dead_is_hunter,
        'hunter_name': hunter_name
    }, to=room_code)

@socketio.on('submit_vote')
def handle_vote(data):
    username = data.get('username')
    room_code = data.get('room_code')
    target = data.get('target')

    if room_code not in day_votes: day_votes[room_code] = {}
    day_votes[room_code][username] = target
    
    room = GameRoom.query.filter_by(room_code=room_code).first()
    alive_players = PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()
    alive_count = len(alive_players)
    
    if len(day_votes[room_code]) == alive_count and alive_count > 0:
        vote_counts = {}
        for voter, v_target in day_votes[room_code].items():
            if v_target == "SKIP":
                continue 
                
            voter_user = User.query.filter_by(username=voter).first()
            voter_player = PlayerStatus.query.filter_by(user_id=voter_user.id, room_id=room.id).first()
            weight = 2 if voter_player and voter_player.role_name == 'Mayor' else 1
            
            vote_counts[v_target] = vote_counts.get(v_target, 0) + weight
            
        executed_player = None
        if vote_counts:
            max_votes = max(vote_counts.values())
            tied_players = [p for p, v in vote_counts.items() if v == max_votes]
            if len(tied_players) == 1:
                executed_player = tied_players[0]

        if executed_player:
            target_user = User.query.filter_by(username=executed_player).first()
            if target_user:
                target_player = PlayerStatus.query.filter_by(user_id=target_user.id, room_id=room.id).first()
                if target_player.role_name == 'Fool':
                    emit('game_over', {'winner': 'Fool', 'fool_name': executed_player}, to=room_code)
                    day_votes[room_code] = {}
                    return
                
        final_dead_names, dead_is_hunter, hunter_name = process_deaths(room_code, [executed_player] if executed_player else [])
                
        day_votes[room_code] = {}
        if check_game_over(room_code): return
        
        room.status = 'NIGHT'
        db.session.commit()
        
        extras = room_extras.get(room_code, {})
        extras['night_count'] = extras.get('night_count', 1) + 1
        
        alive_players_now = [User.query.get(p.user_id).username for p in PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()]
        dead_msg = ", ".join(final_dead_names) if final_dead_names else None
        
        for player in PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all():
            user = User.query.get(player.user_id)
            targets = alive_players_now if player.role_name in ['Bodyguard', 'Cupid'] else [p for p in alive_players_now if p != user.username]
            emit('start_new_night', {
                'executed_player': dead_msg, 
                'targets': targets, 
                'dead_is_hunter': dead_is_hunter,
                'hunter_name': hunter_name,
                'alive_players': alive_players_now,
                'extra_data': extras
            }, to=f"private_{user.username}")

        dead_players = PlayerStatus.query.filter_by(room_id=room.id, is_alive=False).all()
        for player in dead_players:
            user = User.query.get(player.user_id)
            emit('start_new_night', {
                'executed_player': dead_msg, 
                'targets': [], 
                'dead_is_hunter': dead_is_hunter,
                'hunter_name': hunter_name,
                'alive_players': alive_players_now,
                'extra_data': extras
            }, to=f"private_{user.username}")

@socketio.on('submit_hunter_shoot')
def handle_hunter_shoot(data):
    room_code = data.get('room_code')
    target = data.get('target')
    next_phase = data.get('next_phase')
    room = GameRoom.query.filter_by(room_code=room_code).first()
    if not room:
        return
    
    initial_dead = []
    if target and target != "SKIP":
        initial_dead.append(target)
        
    final_dead_names, dead_is_hunter, hunter_name = process_deaths(room_code, initial_dead)
    if check_game_over(room_code): return
    
    extras = room_extras.get(room_code, {})
    alive_players_now = [User.query.get(p.user_id).username for p in PlayerStatus.query.filter_by(room_id=room.id, is_alive=True).all()]
    dead_msg = ", ".join(final_dead_names) if final_dead_names else None
    
    emit('hunter_result', {
        'target': target,
        'final_dead_names': dead_msg,
        'next_phase': next_phase, 
        'alive_players': alive_players_now,
        'dead_is_hunter': dead_is_hunter,
        'hunter_name': hunter_name,
        'extra_data': extras
    }, to=room_code)

@socketio.on('send_message')
def handle_chat(data):
    room_code = data.get('room_code')
    username = data.get('username')
    message = data.get('message')
    is_wolf_chat = data.get('is_wolf_chat', False)
    
    if is_wolf_chat:
        room = GameRoom.query.filter_by(room_code=room_code).first()
        # ทั้งหมาป่าปกติ และ จ่าฝูงหมาป่า จะเห็นแชทนี้
        wolves = PlayerStatus.query.filter(PlayerStatus.room_id == room.id, PlayerStatus.role_name.in_(['Werewolf', 'Alpha Wolf'])).all()
        for wolf in wolves:
            w_user = User.query.get(wolf.user_id)
            emit('receive_message', {'sender': username, 'message': message, 'is_wolf': True}, to=f"private_{w_user.username}")
    else:
        emit('receive_message', {'sender': username, 'message': message, 'is_wolf': False}, to=room_code)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)