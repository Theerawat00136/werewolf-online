from extensions import db
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    # สมมติว่าระบบเข้าสู่ระบบแบบง่ายๆ ไม่ต้องมีพาสเวิร์ดซับซ้อนเพื่อความรวดเร็วในการเล่น
    
    # สถิติ
    games_played = db.Column(db.Integer, default=0)
    games_won = db.Column(db.Integer, default=0)

class GameRoom(db.Model):
    __tablename__ = 'game_rooms'
    
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(10), unique=True, nullable=False)
    host_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    # สถานะ: LOBBY, NIGHT, DAY, VOTING, RUNOFF_VOTING, ENDED
    status = db.Column(db.String(20), default='LOBBY') 
    day_count = db.Column(db.Integer, default=1)
    
    # โครงสร้าง Deck ที่ Host เลือก: {"Werewolf": 2, "Seer": 1, ...}
    selected_roles = db.Column(JSONB, default=lambda: {})
    
    # บันทึกไทม์ไลน์หลังจบเกม: [{"time": "คืนที่ 1", "event": "ด็อพเพิลฯ สวมรอย..."}, ...]
    action_logs = db.Column(JSONB, default=lambda: [])
    
    # เก็บ Index สำหรับรันคิวกลางคืนอัตโนมัติ
    current_action_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PlayerStatus(db.Model):
    __tablename__ = 'player_statuses'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('game_rooms.id'), nullable=False)
    
    role_name = db.Column(db.String(50), nullable=True) # จะถูกอัปเดตตอนสุ่มไพ่
    is_alive = db.Column(db.Boolean, default=True)
    has_used_ability = db.Column(db.Boolean, default=False)
    
    # เก็บสถานะพิเศษ (Tag) ของผู้เล่น เช่น โดนใบ้, โดนคุ้มกัน, เป็นคู่รัก
    # {"is_protected": False, "is_silenced": False, "lovers_with": None}
    status_effects = db.Column(JSONB, default=lambda: {})
    
    # Session Token สำหรับจัดการเรื่องเน็ตหลุด (Reconnection)
    session_token = db.Column(db.String(128), unique=True, nullable=True)