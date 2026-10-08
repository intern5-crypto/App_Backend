# CRITICAL: eventlet must be imported and monkey-patched before any other imports
import eventlet
eventlet.monkey_patch()

import os
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient
import certifi
from datetime import datetime
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'my_secret_key'

# --- SCALABILITY ENHANCEMENTS ---
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode="eventlet",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=20 * 1024 * 1024 
)

# --- DATABASE CONNECTION ---
MONGO_URI = "mongodb+srv://internshipcrypto_db_user:vduMLTN62orV1XA1@cluster0.cbmvnom.mongodb.net/"
client = MongoClient(
    MONGO_URI, 
    tlsCAFile=certifi.where(),
    maxPoolSize=1000,
    waitQueueTimeoutMS=5000
)
db = client.chat_database

print("\n" + "="*75)
print("🚀 MONGODB CONNECTED: HIGH CONCURRENCY MODE")
db.users.update_many({}, {"$set": {"online": False, "sid": None}})
print("🧹 Cleared leftover online sessions")
print("="*75 + "\n")

# --- HTTP API ROUTES ---

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": True, "message": "High-Performance Chat Backend Running!"}), 200

@app.route("/signup", methods=["POST"])
def api_signup():
    try:
        data = request.get_json()
        phone = data.get("phone")
        password = data.get("password")
        name = data.get("name")
        image = data.get("image")
        about = data.get("about", "Hey there! I am using ChatApp.")
        
        if not phone or not password:
            return jsonify({"status": False, "message": "Phone and password are required"}), 400
        
        if db.users.find_one({"phone": phone}):
            return jsonify({"status": False, "message": "User already exists"}), 409
            
        hashed_password = generate_password_hash(password)
        user_id = db.users.insert_one({
            "phone": phone,
            "password": hashed_password,
            "name": name,
            "image": image,
            "about": about,
            "online": False,
            "sid": None,
            "identityPublic": None,       
            "signedPreKeyPublic": None,
            "pqPreKeyPublic": None 
        }).inserted_id
        
        return jsonify({"status": True, "message": "User created", "userId": str(user_id), "name": name}), 201
    except Exception as e:
        return jsonify({"status": False, "message": "Internal Server Error"}), 500

@app.route("/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json()
        phone = data.get("phone")
        password = data.get("password")
        
        user = db.users.find_one({"phone": phone})
        
        if user and check_password_hash(user['password'], password):
            db.users.update_one({"_id": user["_id"]}, {"$set": {"online": False, "sid": None}})
            return jsonify({
                "status": True,
                "userId": str(user["_id"]),
                "name": user.get("name"),
                "image": user.get("image"),
                "about": user.get("about", "Available")
            }), 200
            
        return jsonify({"status": False, "message": "Invalid Credentials"}), 401
    except Exception as e:
        return jsonify({"status": False, "message": "Internal Server Error"}), 500

@app.route("/update_profile", methods=["POST"])
def update_profile():
    try:
        data = request.get_json()
        user_id = data.get("userId")
        image = data.get("image")
        
        if user_id and image:
            db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"image": image}})
            return jsonify({"status": True, "message": "Profile updated successfully"})
        return jsonify({"status": False, "message": "Missing Data"}), 400
    except Exception as e:
        return jsonify({"status": False, "message": "Internal Server Error"}), 500

@app.route("/users", methods=["GET"])
def get_users():
    try:
        current_user_id = request.args.get("userId")
        
        current_phone = None
        if current_user_id and ObjectId.is_valid(current_user_id):
            current_user = db.users.find_one({"_id": ObjectId(current_user_id)})
            if current_user:
                current_phone = current_user.get("phone")
            query = {"_id": {"$ne": ObjectId(current_user_id)}}
        else:
            query = {}
        
        users_list = []
        for user in db.users.find(query):
            peer_phone = user.get("phone")
            unread_count = 0
            last_activity = 0 
            
            if current_phone and peer_phone:
                unread_count = db.messages.count_documents({
                    "sender": peer_phone,
                    "receiver": current_phone,
                    "status": {"$lt": 3}
                })
                
                last_msg = db.messages.find_one({
                    "$or": [
                        {"sender": peer_phone, "receiver": current_phone},
                        {"sender": current_phone, "receiver": peer_phone}
                    ]
                }, sort=[("timestamp", -1)])
                
                if last_msg:
                    last_activity = last_msg["timestamp"].timestamp()
                
            users_list.append({
                "id": str(user["_id"]),
                "name": user.get("name"),
                "phone": peer_phone,
                "image": user.get("image"),
                "about": user.get("about", "Available"),
                "online": user.get("online", False),
                "unreadCount": unread_count,
                "lastActivity": last_activity
            })
            
        users_list.sort(key=lambda x: x["lastActivity"], reverse=True)
        
        return jsonify(users_list), 200
    except Exception as e:
        return jsonify([]), 500

@app.route("/messages", methods=["GET"])
def get_messages():
    try:
        sender = request.args.get("sender")
        receiver = request.args.get("receiver")
        
        messages = list(db.messages.find({
            "$or": [
                {"sender": sender, "receiver": receiver},
                {"sender": receiver, "receiver": sender}
            ]
        }).sort("timestamp", 1))

        output = []
        for msg in messages:
            msg_data = {k: v for k, v in msg.items() if k not in ["_id", "timestamp"]}
            msg_data["dateTime"] = msg["timestamp"].strftime("%I:%M %p")
            output.append(msg_data)
            
        return jsonify(output), 200
    except Exception as e:
        return jsonify([]), 500


# --- HYBRID X3DH KEY REGISTRY ENDPOINTS ---

@app.route("/keys/upload", methods=["POST"])
def upload_keys():
    try:
        data = request.get_json(force=True) 
        phone = data.get("phone")
        
        identity_public = data.get("identityPublic") or data.get("identityKey") or data.get("identity_public")
        signed_prekey_public = data.get("signedPreKeyPublic") or data.get("signedPreKey") or data.get("signed_prekey_public")
        pq_prekey_public = data.get("pqPreKeyPublic")

        if not phone or not identity_public or not signed_prekey_public or not pq_prekey_public:
            return jsonify({"status": False, "message": "Missing key data"}), 400

        db.users.update_one(
            {"phone": phone},
            {"$set": {
                "identityPublic": identity_public,
                "signedPreKeyPublic": signed_prekey_public,
                "pqPreKeyPublic": pq_prekey_public
            }}
        )

        print("\n" + "="*75)
        print(f"🔑 [KEY REGISTRY] Cryptographic PreKey Bundle Uploaded for: {phone}")
        print("="*75)
        print("  1. X25519 Curve25519 (Identity Key):")
        print("     └─ Use Case: Long-term device authenticity & sender verification")
        print("  2. X25519 Curve25519 (Signed PreKey):")
        print("     └─ Use Case: Asynchronous offline key agreement (X3DH DH1 & DH3)")
        print("  3. CRYSTALS-Kyber-768 / NIST ML-KEM (PQ PreKey):")
        print("     └─ Use Case: Post-Quantum lattice key encapsulation (resists quantum computers)")
        print("="*75 + "\n")

        return jsonify({"status": True, "message": "Keys uploaded successfully"}), 200
    except Exception as e:
        return jsonify({"status": False, "message": "Internal Server Error"}), 500

@app.route("/keys", methods=["GET"])
def get_keys():
    try:
        phone = request.args.get("phone") 
        user = db.users.find_one({"phone": phone})
        if user and user.get("identityPublic") and user.get("pqPreKeyPublic"):
            print("\n" + "-"*75)
            print(f"📡 [KEY EXCHANGE] Public PreKey Bundle fetched for peer: {phone}")
            print("-" * 75 + "\n")
            return jsonify({
                "phone": user["phone"],
                "identityPublic": user["identityPublic"],
                "signedPreKeyPublic": user["signedPreKeyPublic"],
                "pqPreKeyPublic": user["pqPreKeyPublic"], 
                "identity_public": user["identityPublic"],
                "signed_prekey_public": user["signedPreKeyPublic"]
            }), 200
        return jsonify({"status": False, "message": "Keys not found"}), 404
    except Exception as e:
        return jsonify({"status": False, "message": "Internal Server Error"}), 500


# --- SOCKET.IO EVENTS ---

@socketio.on("connect")
def handle_connect():
    pass # Managed in register

@socketio.on("disconnect")
def handle_disconnect():
    user = db.users.find_one({"sid": request.sid})
    if user:
        phone = user.get("phone", "Unknown")
        name = user.get("name", "Unknown")
        db.users.update_one({"_id": user["_id"]}, {"$set": {"online": False, "sid": None}})
        
        print("\n" + "="*75)
        print(f"🔴 [STATUS: OFFLINE] User Disconnected")
        print("="*75)
        print(f"  ├─ Name:  {name}")
        print(f"  ├─ Phone: {phone}")
        print(f"  └─ SID:   {request.sid}")
        print("="*75 + "\n")

@socketio.on("register")
def handle_register(data):
    phone = data.get("phone")
    if phone:
        join_room(phone) 
        db.users.update_one({"phone": phone}, {"$set": {"online": True, "sid": request.sid}})
        user = db.users.find_one({"phone": phone})
        name = user.get("name", "Unknown") if user else "Unknown"
        
        print("\n" + "="*75)
        print(f"🟢 [STATUS: ONLINE] User Registered to Socket")
        print("="*75)
        print(f"  ├─ Name:  {name}")
        print(f"  ├─ Phone: {phone}")
        print(f"  └─ SID:   {request.sid}")
        print("="*75 + "\n")

@socketio.on("fetch_pending")
def handle_fetch_pending(data):
    phone = data.get("phone")
    peer_phone = data.get("peerPhone")
    if phone and peer_phone:
        pending_msgs = list(db.messages.find({
            "receiver": phone,
            "sender": peer_phone,
            "status": {"$lt": 3}
        }).sort("timestamp", 1))
        
        if pending_msgs:
            print("\n" + "="*75)
            print(f"📬 [LETTERBOX: SYNC] Delivering Offline Messages")
            print("="*75)
            print(f"  ├─ From:  {peer_phone}")
            print(f"  ├─ To:    {phone} (Just came online)")
            print(f"  └─ Count: {len(pending_msgs)} unread message(s)")
            print("="*75 + "\n")
            
            for msg in pending_msgs:
                delivery_payload = {k: v for k, v in msg.items() if k not in ["_id", "timestamp"]}
                delivery_payload["dateTime"] = msg["timestamp"].strftime("%I:%M %p")
                emit("receive_message", delivery_payload, room=phone)

@socketio.on("send_message")
def handle_message(data):
    sender = data.get("sender")    
    receiver = data.get("receiver") 
    msg_type = data.get("type", "text")
    timestamp = datetime.now()
    
    is_handshake = "x3dh_ek" in data and "pq_ct" in data
    
    print("\n" + "="*75)
    if is_handshake:
        print(f"🔐 [E2EE PROTOCOL] Initial Hybrid Handshake & Message: {sender} -> {receiver}")
        print("="*75)
        print("  Stage 1: Hybrid X3DH Asynchronous Key Exchange")
        print("   ├─ X25519 ECDH (Ephemeral & Identity Keys):")
        print("   │  └─ Use Case: Classical Triple-DH (DH1, DH2, DH3) for mutual auth & PFS")
        print("   ├─ CRYSTALS-Kyber-768 / NIST ML-KEM (Ciphertext attached):")
        print("   │  └─ Use Case: Post-Quantum KEM (protects against quantum harvest attacks)")
        print("   └─ HKDF-SHA256:")
        print("      └─ Use Case: Extract & expand (DH1 || DH2 || DH3 || PQ_SS) -> Master Root Key")
        print("  Stage 2: Double Ratchet & Payload Protection")
        print("   ├─ HMAC-SHA256:")
        print("   │  └─ Use Case: Symmetric Ratchet step (derives per-message Message Key)")
        print("   └─ AES-256-GCM (128-bit Tag, 12-byte Nonce):")
        print(f"      └─ Use Case: AEAD encryption for [{msg_type}] payload & RatchetHeader integrity")
    else:
        print(f"💬 [E2EE PROTOCOL] Double Ratchet Message: {sender} -> {receiver}")
        print("="*75)
        print("  Stage: Active Double Ratchet Session")
        print("   ├─ X25519 ECDH (DH Ratchet Key attached):")
        print("   │  └─ Use Case: Asymmetric Ratchet step (self-healing / post-compromise security)")
        print(f"   ├─ HMAC-SHA256 (Ratchet Index: n={data.get('n', 0)}, pn={data.get('pn', 0)}):")
        print("   │  └─ Use Case: Symmetric chain key advancement (forward secrecy)")
        print("   └─ AES-256-GCM:")
        print(f"      └─ Use Case: Authenticated encryption of [{msg_type}] payload")
    
    db_payload = data.copy()
    db_payload["timestamp"] = timestamp
    db_payload["status"] = 1
    
    msg_id = db.messages.insert_one(db_payload).inserted_id

    receiver_user = db.users.find_one({"phone": receiver})
    
    # 🟢 DELIVERY LOGIC 🟢
    if receiver_user and receiver_user.get("online"):
        emit_payload = data.copy()
        emit_payload["dateTime"] = timestamp.strftime("%I:%M %p")
        
        emit("receive_message", emit_payload, room=receiver)
        emit("message_status", {"status": 2}, room=sender)
        
        print("\n" + "-"*75)
        print(f"⚡ [DELIVERY: LIVE] Message routed directly to active socket.")
        print(f"  ├─ To:   {receiver}")
        print(f"  └─ Room: {receiver} (Active)")
        print("-" * 75)
    else:
        emit("message_status", {"status": 1}, room=sender)
        
        print("\n" + "-"*75)
        print(f"⏳ [DELIVERY: STORED] User offline. Message saved to Letterbox.")
        print(f"  ├─ To:     {receiver}")
        print(f"  └─ Status: Pending Delivery")
        print("-" * 75)
        
    print("="*75 + "\n")

@socketio.on("message_read")
def handle_read(data):
    sender = data.get("sender")    
    receiver = data.get("receiver") 
    
    result = db.messages.update_many(
        {"sender": sender, "receiver": receiver, "status": {"$lt": 3}},
        {"$set": {"status": 3}}
    )
    if result.modified_count > 0:
        emit("message_status", {"status": 3}, room=sender)
        
        print("\n" + "="*75)
        print(f"👀 [READ RECEIPT] Messages Marked as Read")
        print("="*75)
        print(f"  ├─ Reader: {receiver}")
        print(f"  ├─ Sender: {sender}")
        print(f"  └─ Count:  {result.modified_count} message(s) updated to Status 3 (Blue Ticks)")
        print("="*75 + "\n")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 SERVER STARTING ON PORT {port} WITH EVENTLET WORKERS...")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, use_reloader=False)