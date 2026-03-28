"""
Seed realistic demo data for a mid-size hotel (100 rooms, ~60% occupancy).
~60 guests/night, ~25% text = ~15 conversations/day = ~450/month, ~1,500 messages.
Run once: python3 seed_demo.py
"""
import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "hotel.db"
HOTEL_ID = 1

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

now = datetime.utcnow()

# ── Conversation templates ───────────────────────────────────────────────────
# (guest_msg, ai_reply, task_summary, dept, priority, pct_chance_of_task)
TEMPLATES = [
    # AI-resolved (no task)
    ("What's the wifi password?",
     "The WiFi network is 'DemoHotel_Guest' and the password is Welcome2024!",
     None, None, None, 0),
    ("What time is checkout?",
     "Standard checkout is at 11:00 AM. Need a late checkout? Just ask!",
     None, None, None, 0),
    ("What time is breakfast?",
     "Breakfast is served 7:00–10:30 AM in the Garden Restaurant on the ground floor.",
     None, None, None, 0),
    ("Is there a pool?",
     "Yes! Our heated outdoor pool is open 7 AM–10 PM daily.",
     None, None, None, 0),
    ("Is there a gym?",
     "Our fitness center is on the 2nd floor, open 24 hours. Towels and water provided.",
     None, None, None, 0),
    ("Is parking free?",
     "Self-parking is complimentary for hotel guests. Garage entrance is on the north side.",
     None, None, None, 0),
    ("What time does the bar close?",
     "The bar is open until midnight Sunday–Thursday, and 1 AM on Fridays and Saturdays.",
     None, None, None, 0),
    ("Is there a spa?",
     "Yes, our spa is on the 3rd floor, open 9 AM–8 PM. Book at the front desk or reply here.",
     None, None, None, 0),
    ("What restaurants are nearby?",
     "Great options nearby: Rosario's Italian (2 min walk), The Harbor Grill (5 min), Blue Lotus Asian (8 min).",
     None, None, None, 0),
    ("Is there a drugstore nearby?",
     "There's a CVS about 3 minutes walk on Main Street, open until 10 PM.",
     None, None, None, 0),
    ("Do you have room service?",
     "Yes! Room service is available 6 AM–midnight. Call ext. 0 or reply here with your order.",
     None, None, None, 0),
    ("Is the pool heated?",
     "Yes, our pool is heated year-round at 82°F. Hot tub is also available same hours.",
     None, None, None, 0),
    ("What floor is my room on?",
     "You can check your key card envelope for your floor, or call the front desk at ext. 0.",
     None, None, None, 0),
    ("Where can I find ice?",
     "Ice machines are located on every even-numbered floor near the elevators.",
     None, None, None, 0),
    ("Do you have a business center?",
     "Yes, our business center is on the lobby level, open 7 AM–10 PM. Printing and WiFi available.",
     None, None, None, 0),
    ("How far is the airport?",
     "The airport is about 18 miles away, roughly 25 minutes by taxi or rideshare.",
     None, None, None, 0),
    ("Is there a shuttle to downtown?",
     "Complimentary shuttle runs every hour on the half-hour, 8 AM–10 PM. Meet at the main entrance.",
     None, None, None, 0),
    ("Can I store my luggage after checkout?",
     "Absolutely! Bring your bags to the front desk and we'll hold them for you.",
     None, None, None, 0),

    # Task-creating (housekeeping)
    ("Can you bring extra towels?",
     "Of course! We'll have extra towels sent up shortly.",
     "Extra towels requested", "housekeeping", "normal", 100),
    ("I need more pillows please",
     "We'll have extra pillows brought to your room right away.",
     "Extra pillows requested", "housekeeping", "low", 100),
    ("Can I get an extra blanket?",
     "Absolutely! We'll send one up shortly.",
     "Extra blanket requested", "housekeeping", "low", 100),
    ("The room hasn't been cleaned today",
     "I apologize for the delay — I've sent housekeeping to your room right away.",
     "Room not cleaned — send housekeeping", "housekeeping", "high", 100),
    ("Can you bring more shampoo and soap?",
     "Of course! Housekeeping will bring toiletries up shortly.",
     "Toiletries restocked requested", "housekeeping", "low", 100),
    ("I need a crib for my baby",
     "We'll have a crib delivered to your room within 30 minutes.",
     "Crib delivery requested", "housekeeping", "normal", 100),
    ("Can I get a toothbrush and toothpaste?",
     "Housekeeping will bring those right up!",
     "Toothbrush/toothpaste requested", "housekeeping", "low", 100),
    ("Can you remove the dishes from my room?",
     "Of course — someone will come collect them shortly.",
     "Dish pickup requested", "housekeeping", "low", 100),

    # Task-creating (maintenance)
    ("The AC in my room isn't working",
     "I'm sorry — maintenance has been notified and will be up shortly.",
     "AC not working in guest room", "maintenance", "high", 100),
    ("The shower has no hot water",
     "Apologies! Maintenance will be there within 15 minutes.",
     "No hot water in guest shower", "maintenance", "high", 100),
    ("My TV isn't turning on",
     "I'll send maintenance to take a look right away.",
     "TV not working in guest room", "maintenance", "normal", 100),
    ("There's a leak under the bathroom sink",
     "I've flagged this urgently — maintenance is on their way.",
     "Leak under bathroom sink", "maintenance", "urgent", 100),
    ("The lights in my room keep flickering",
     "Maintenance has been notified and will be up to check shortly.",
     "Flickering lights in guest room", "maintenance", "normal", 100),
    ("My room key isn't working",
     "Please come to the front desk and we'll reprogram your key card right away.",
     None, None, None, 0),
    ("The toilet is clogged",
     "I'm sorry! Maintenance is being sent up right now.",
     "Clogged toilet in guest room", "maintenance", "urgent", 100),

    # Task-creating (frontdesk)
    ("Can I get a late checkout until 1pm?",
     "I've sent your late checkout request to the front desk — we'll confirm availability shortly.",
     "Late checkout request until 1pm", "frontdesk", "normal", 100),
    ("Can I get a late checkout until 2pm?",
     "Passing this to the front desk now — we'll confirm shortly.",
     "Late checkout request until 2pm", "frontdesk", "normal", 100),
    ("I locked myself out of my room",
     "A staff member will be at your room within 5 minutes!",
     "Guest locked out — send staff", "frontdesk", "high", 100),
    ("There's a noise complaint — the room next door is very loud",
     "I'm sorry for the disturbance — security has been notified.",
     "Noise complaint from guest", "frontdesk", "high", 100),
    ("I have a question about my bill",
     "I'll have the front desk follow up with you shortly about your billing.",
     "Guest has billing question", "frontdesk", "normal", 100),
    ("Can I get a wake-up call at 6am?",
     "Wake-up call logged for 6:00 AM. Is there anything else you need?",
     "Wake-up call at 6am", "frontdesk", "low", 100),
    ("Can I get a wake-up call at 7am?",
     "Wake-up call logged for 7:00 AM tomorrow morning!",
     "Wake-up call at 7am", "frontdesk", "low", 100),

    # Task-creating (concierge)
    ("Can you book a taxi to the airport at 5am?",
     "Passing this to our concierge to arrange — they'll confirm shortly.",
     "Taxi to airport at 5am", "concierge", "normal", 100),
    ("Can you book a dinner reservation for 2 at 7pm?",
     "I'll have our concierge check availability and confirm.",
     "Dinner reservation: 2 guests at 7pm", "concierge", "normal", 100),
    ("Can you recommend and book a tour?",
     "Our concierge will reach out shortly with some great options.",
     "Guest wants tour recommendations/booking", "concierge", "low", 100),
    ("I need help with luggage to check out",
     "A bellhop will be at your room shortly to assist!",
     "Luggage assist for checkout", "concierge", "normal", 100),
    ("Can you get theater tickets for tonight?",
     "I'll pass this to our concierge right away.",
     "Theater tickets requested for tonight", "concierge", "normal", 100),
]

HOUR_WEIGHTS = [
    1, 0, 0, 0, 0, 1, 2, 5, 7, 6, 4, 3,   # 12am–11am
    3, 3, 2, 2, 3, 5, 7, 9, 8, 6, 4, 2    # 12pm–11pm
]

def random_hour():
    return random.choices(range(24), weights=HOUR_WEIGHTS)[0]

def random_msg_dt(base_dt):
    offset_hours = random.uniform(1, 22)
    hour = random_hour()
    return base_dt.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))

ROOMS = [
    "101","102","103","104","105","106","107","108","109","110",
    "201","202","203","204","205","206","207","208","209","210",
    "301","302","303","304","305","306","307","308","309","310",
    "401","402","403","404","405","406","407","408","409","410",
    "501","502","503","504","505","506","507","508","509","510",
    "601","602","603","604","605","606","607","608","609","610",
]

STAFF_REPLIES = [
    "All taken care of! Let us know if you need anything else.",
    "Done! Is there anything else we can help with?",
    "We're on it — someone will be there shortly.",
    "All sorted! Feel free to reach out anytime.",
    "Handled! Let us know if there's anything else.",
    "We've got that covered for you.",
    "On it right now — thanks for letting us know!",
]

print("Seeding demo data — this may take a moment...")

# Clear any previous seed data for guests with fake phone pattern
cur.execute("SELECT id FROM guests WHERE phone LIKE '+1555%'")
old_guest_ids = [r["id"] for r in cur.fetchall()]
if old_guest_ids:
    placeholders = ",".join("?" * len(old_guest_ids))
    cur.execute(f"SELECT id FROM stays WHERE guest_id IN ({placeholders})", old_guest_ids)
    old_stay_ids = [r["id"] for r in cur.fetchall()]
    if old_stay_ids:
        sp = ",".join("?" * len(old_stay_ids))
        cur.execute(f"DELETE FROM tasks WHERE stay_id IN ({sp})", old_stay_ids)
        cur.execute(f"DELETE FROM messages WHERE stay_id IN ({sp})", old_stay_ids)
        cur.execute(f"DELETE FROM stays WHERE id IN ({sp})", old_stay_ids)
    cur.execute(f"DELETE FROM guests WHERE id IN ({placeholders})", old_guest_ids)
    print(f"Cleared {len(old_guest_ids)} previous demo guests.")

# Generate ~800 stays across 30 days (~27/day)
NUM_STAYS = 800
phone_counter = 2000
guests_created = stays_created = msgs_created = tasks_created = 0

for i in range(NUM_STAYS):
    phone = f"+1555{str(phone_counter + i).zfill(7)}"

    # Create guest
    cur.execute("INSERT INTO guests (phone, created_at) VALUES (?, ?)",
                (phone, ts(now - timedelta(days=35))))
    guest_id = cur.lastrowid
    guests_created += 1

    # Spread stays evenly across 30 days with some randomness
    days_ago = random.uniform(0.5, 30)
    stay_dt = now - timedelta(days=days_ago)
    stay_dt = stay_dt.replace(hour=random.randint(14, 17), minute=random.randint(0, 59))  # check-in around 2-5pm
    checkout_dt = stay_dt + timedelta(days=random.randint(1, 6))
    room = random.choice(ROOMS)

    cur.execute(
        """INSERT INTO stays (guest_id, hotel_id, status, room_number, check_out_date, created_at)
           VALUES (?, ?, 'active', ?, ?, ?)""",
        (guest_id, HOTEL_ID, room, ts(checkout_dt), ts(stay_dt))
    )
    stay_id = cur.lastrowid
    stays_created += 1

    # Each stay sends 2–5 messages across different times
    # Weight toward AI-only templates (first 18) for realistic resolution rate
    num_conversations = random.randint(2, 5)
    ai_templates = TEMPLATES[:18]
    task_templates = TEMPLATES[18:]
    # Pick 2-3 AI-only + 0-2 task-creating per stay
    n_ai = min(random.randint(2, 3), len(ai_templates))
    n_task = min(random.randint(0, 2), len(task_templates))
    used_templates = random.sample(ai_templates, n_ai) + random.sample(task_templates, n_task)

    for tmpl in used_templates:
        guest_msg, ai_reply, task_summary, dept, priority, task_pct = tmpl

        msg_dt = random_msg_dt(stay_dt)
        if msg_dt > now:
            msg_dt = now - timedelta(minutes=random.randint(5, 60))

        # Guest inbound
        cur.execute(
            "INSERT INTO messages (stay_id, direction, source, body, created_at) VALUES (?, 'inbound', 'guest', ?, ?)",
            (stay_id, guest_msg, ts(msg_dt))
        )
        inbound_id = cur.lastrowid
        msgs_created += 1

        # AI reply
        if ai_reply:
            ai_dt = msg_dt + timedelta(seconds=random.randint(3, 10))
            cur.execute(
                "INSERT INTO messages (stay_id, direction, source, body, created_at) VALUES (?, 'outbound', 'ai', ?, ?)",
                (stay_id, ai_reply, ts(ai_dt))
            )
            msgs_created += 1

        # Task
        if task_summary and random.randint(1, 100) <= task_pct:
            task_dt = msg_dt + timedelta(seconds=random.randint(5, 15))
            resolved = random.random() > 0.15  # 85% resolved

            if resolved:
                resolution_minutes = random.randint(8, 90)
                completed_dt = task_dt + timedelta(minutes=resolution_minutes)
                cur.execute(
                    """INSERT INTO tasks (stay_id, type, status, summary, department, priority,
                       created_from_message_id, completed_at, created_at)
                       VALUES (?, 'guest_request', 'done', ?, ?, ?, ?, ?, ?)""",
                    (stay_id, task_summary, dept, priority, inbound_id, ts(completed_dt), ts(task_dt))
                )
                # Staff reply after completing
                if random.random() > 0.3:
                    staff_dt = msg_dt + timedelta(minutes=random.randint(2, 30))
                    cur.execute(
                        "INSERT INTO messages (stay_id, direction, source, body, created_at) VALUES (?, 'outbound', 'staff', ?, ?)",
                        (stay_id, random.choice(STAFF_REPLIES), ts(staff_dt))
                    )
                    msgs_created += 1
            else:
                cur.execute(
                    """INSERT INTO tasks (stay_id, type, status, summary, department, priority,
                       created_from_message_id, created_at)
                       VALUES (?, 'guest_request', 'open', ?, ?, ?, ?, ?)""",
                    (stay_id, task_summary, dept, priority, inbound_id, ts(task_dt))
                )
            tasks_created += 1

conn.commit()
conn.close()

print(f"Done!")
print(f"  Guests:   {guests_created}")
print(f"  Stays:    {stays_created}")
print(f"  Messages: {msgs_created}")
print(f"  Tasks:    {tasks_created}")
