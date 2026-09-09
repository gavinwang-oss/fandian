"""
Agentic eval loop for decide_action_llm routing logic.

Runs 100 realistic guest messages through the routing prompt,
scores accuracy, prints failures, then uses the LLM to suggest
prompt improvements based on what failed.

Usage:
    python3 eval_routing.py
    python3 eval_routing.py --suggest   # also generate prompt improvement suggestions
"""
import argparse
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL   = os.getenv("OPENAI_MODEL", "gpt-4.1")

# ── Simulated hotel context ──────────────────────────────────────────────────
HOTEL_INFO = {
    "hotel_name":       "Demo Hotel",
    "checkin_time":     "3:00 PM",
    "checkout_time":    "11:00 AM",
    "wifi_network":     "DemoHotel_Guest",
    "wifi_password":    "Welcome2024",
    "breakfast_hours":  "7:00 AM – 10:30 AM",
    "pool_hours":       "7:00 AM – 10:00 PM",
    "gym_hours":        "24 hours",
    "parking":          "Complimentary self-parking, garage on north side",
    "spa_hours":        "9:00 AM – 8:00 PM, 3rd floor",
}

# ── Test cases ───────────────────────────────────────────────────────────────
# (message, expected_action, category, notes)
TEST_CASES = [
    # ── Should be REPLY ─────────────────────────────────────────────────────
    ("What's the wifi password?",               "reply", "info", "Direct info question"),
    ("What time is checkout?",                  "reply", "info", "Direct info question"),
    ("What time is breakfast?",                 "reply", "info", "Direct info question"),
    ("Is there a pool?",                        "reply", "info", "Yes/no + hours"),
    ("Is the pool heated?",                     "reply", "info", "General knowledge"),
    ("Is there a gym?",                         "reply", "info", "Direct info question"),
    ("Is parking free?",                        "reply", "info", "Direct info question"),
    ("What floor is the spa on?",               "reply", "info", "Direct info question"),
    ("What time does the spa close?",           "reply", "info", "Direct info question"),
    ("Do you have room service?",               "reply", "info", "General hotel knowledge"),
    ("What time is check-in?",                  "reply", "info", "Direct info question"),
    ("Is there a bar?",                         "reply", "info", "General hotel knowledge"),
    ("Do you have a business center?",          "reply", "info", "General hotel knowledge"),
    ("Where can I find ice?",                   "reply", "info", "General hotel knowledge"),
    ("How far is the airport?",                 "reply", "info", "General knowledge"),
    ("Is there a shuttle to downtown?",         "reply", "info", "General hotel knowledge"),
    ("What restaurants are nearby?",            "reply", "info", "Concierge info"),
    ("Is there a drugstore nearby?",            "reply", "info", "General local knowledge"),
    ("Can I store my luggage after checkout?",  "reply", "info", "Policy question"),
    ("What's the cancellation policy?",         "reply", "info", "Policy — should reply not invent"),
    ("Is the gym open 24 hours?",               "reply", "info", "Direct info question"),
    ("Is breakfast included?",                  "reply", "info", "Should answer from info"),
    ("What's the pool temperature?",            "reply", "info", "General knowledge"),
    ("Do you have a hot tub?",                  "reply", "info", "General hotel knowledge"),
    ("Are pets allowed?",                       "reply", "info", "Policy — should reply not invent"),
    ("Is smoking allowed in rooms?",            "reply", "info", "Policy question"),
    ("Do you have valet parking?",              "reply", "info", "General hotel service"),
    ("Is there an EV charger?",                 "reply", "info", "Should answer honestly if unknown"),
    ("Where is the elevator?",                  "reply", "info", "General hotel knowledge"),
    ("Can I get a late checkout?",              "task", "frontdesk", "Clear checkout request even if phrased as question"),

    # ── Should be TASK ──────────────────────────────────────────────────────
    ("Can you bring extra towels to my room?",          "task", "housekeeping", "Physical delivery"),
    ("I need more pillows",                             "task", "housekeeping", "Physical delivery"),
    ("Can I get an extra blanket?",                     "task", "housekeeping", "Physical delivery"),
    ("The room hasn't been cleaned today",              "task", "housekeeping", "Service failure"),
    ("Can you send up some shampoo and conditioner?",   "task", "housekeeping", "Toiletries delivery"),
    ("I need a crib for my baby",                       "task", "housekeeping", "Equipment delivery"),
    ("Can you remove the dirty dishes from my room?",   "task", "housekeeping", "Pickup task"),
    ("The AC isn't working",                            "task", "maintenance",  "Repair needed"),
    ("There's no hot water in my shower",               "task", "maintenance",  "Repair needed"),
    ("My TV won't turn on",                             "task", "maintenance",  "Repair needed"),
    ("There's a leak under my sink",                    "task", "maintenance",  "Urgent repair"),
    ("The lights keep flickering",                      "task", "maintenance",  "Repair needed"),
    ("The toilet is clogged",                           "task", "maintenance",  "Urgent repair"),
    ("My key card stopped working",                     "task", "frontdesk",    "Access issue requiring staff"),
    ("I locked myself out of my room",                  "task", "frontdesk",    "Urgent access issue"),
    ("The people next door are being really loud",      "task", "frontdesk",    "Noise complaint"),
    ("I have a question about a charge on my bill",     "task", "frontdesk",    "Billing issue"),
    ("Can I get a wake-up call at 6am?",                "task", "frontdesk",    "Service request"),
    ("I'd like to request a late checkout until 1pm",   "task", "frontdesk",    "Actionable request"),
    ("Can you book a taxi for me tomorrow at 5am?",     "task", "concierge",    "Booking request"),
    ("Can you make a dinner reservation for 2 at 7pm?", "task", "concierge",    "Booking request"),
    ("I need help carrying my luggage to checkout",     "task", "concierge",    "Bell service"),
    ("Can you get me theater tickets for tonight?",     "task", "concierge",    "Booking request"),
    ("I'd like a bottle of wine sent to my room",       "task", "frontdesk",    "Room service order"),
    ("Can I get a burger and fries from room service?", "task", "frontdesk",    "Room service order"),
    ("The shower is broken",                            "task", "maintenance",  "Repair needed"),
    ("My room smells like smoke",                       "task", "frontdesk",    "Room issue requiring staff"),
    ("There's a bug in my room",                        "task", "housekeeping", "Room issue requiring staff"),
    ("Can someone come fix the safe in my room?",       "task", "maintenance",  "Repair needed"),
    ("I need an iron and ironing board",                "task", "housekeeping", "Equipment delivery"),

    # ── Edge cases — should be REPLY (not task) ──────────────────────────────
    ("What is the name of the employee who will respond to this message?", "reply", "meta", "Nonsensical — no task"),
    ("Are you a real person or AI?",                    "reply", "meta", "Meta question about AI"),
    ("Hello",                                           "reply", "meta", "Greeting — no task"),
    ("Hi there",                                        "reply", "meta", "Greeting — no task"),
    ("Thanks!",                                         "reply", "meta", "Acknowledgment — no task"),
    ("Thank you so much!",                              "reply", "meta", "Acknowledgment — no task"),
    ("OK got it",                                       "reply", "meta", "Acknowledgment — no task"),
    ("Sounds good",                                     "reply", "meta", "Acknowledgment — no task"),
    ("Never mind",                                      "reply", "meta", "Cancellation — no task"),
    ("asdfghjkl",                                       "reply", "meta", "Gibberish — no task"),
    ("Can you do my taxes?",                            "reply", "meta", "Irrelevant — no task"),
    ("What's the meaning of life?",                     "reply", "meta", "Irrelevant — no task"),
    ("Who is the manager on duty?",                     "reply", "meta", "Staff info — no task"),
    ("What's your name?",                               "reply", "meta", "Meta question — no task"),
    ("Test",                                            "reply", "meta", "Test message — no task"),
    ("123",                                             "reply", "meta", "Gibberish — no task"),

    # ── Tricky / ambiguous ───────────────────────────────────────────────────
    ("I want to cancel my reservation",                 "task", "frontdesk",   "Actionable even if hotel can't do it — should escalate"),
    ("Can you recommend a good spa treatment?",         "reply", "info",        "Info/recommendation — not a booking"),
    ("Is it possible to get extra towels?",             "task", "housekeeping", "Phrased as question but clearly a request"),
    ("Do you know if the pool is open right now?",      "reply", "info",        "Info question not a task"),
    ("I think I left my wallet in the restaurant",      "task", "concierge",    "Lost item — needs staff"),
    ("My room is too hot",                              "task", "maintenance",  "Comfort issue requiring adjustment"),
    ("The TV remote doesn't work",                      "task", "maintenance",  "Equipment issue"),
    ("Can I speak to the manager?",                     "task", "frontdesk",    "Escalation request"),
    ("I need to extend my stay by one night",           "task", "frontdesk",    "Reservation change"),
    ("There's a weird smell coming from the vents",     "task", "maintenance",  "Maintenance issue"),
    ("I'm not happy with my room",                      "task", "frontdesk",    "Complaint requiring staff"),
    ("The hairdryer isn't working",                     "task", "maintenance",  "Equipment issue"),
    ("Can I get a toothbrush?",                         "task", "housekeeping", "Toiletries — physical delivery"),
    ("Do you have an airport shuttle?",                 "reply", "info",        "Info question — not a booking"),
    ("I need a wake-up call",                           "task", "frontdesk",    "Service request even without specific time"),
    ("Is there anything fun to do nearby?",             "reply", "info",        "Concierge info — not a booking task"),
    ("Can you check if my package arrived?",            "task", "frontdesk",    "Action required"),
    ("I'd like to upgrade my room",                     "task", "frontdesk",    "Actionable request"),
    ("The elevator is broken",                          "task", "maintenance",  "Infrastructure issue"),
    ("My neighbor's music is too loud",                 "task", "frontdesk",    "Noise complaint"),
]

INSTRUCTIONS = (
    "You are a hotel AI concierge. Classify the guest message into one of two actions:\n"
    "1. action=reply — the guest is asking about hotel policies, services, or general information "
    "and is NOT making a specific request or order. Answer directly using hotel info or general knowledge.\n"
    "2. action=task — the guest needs something done by hotel staff. Use this for any real, actionable "
    "service request including: bring towels/amenities, fix AC or maintenance issues, room service food orders, "
    "billing concerns or disputes, complaints about the room requiring staff attention, booking requests (spa, taxi, dinner), "
    "valet, late checkout if explicitly requested, or anything requiring physical staff action.\n\n"
    "Key distinctions:\n"
    "- 'Can I get a late checkout?' → task (clear request even if phrased as a question)\n"
    "- 'What is your late checkout policy?' → reply (info question only)\n"
    "- 'Do you have room service?' → reply (info question)\n"
    "- 'Can I get a burger and fries?' → task (food order)\n"
    "- 'I have a question about my bill' → task (billing issue needs staff)\n"
    "- 'I'm not happy with my room' → task (complaint needs staff follow-up)\n\n"
    "NEVER create a task for: greetings, thanks, acknowledgments, questions about staff names, "
    "meta questions about the AI, jokes, gibberish, or anything not a real hotel request.\n"
    "Do NOT invent hotel policies or hours not provided.\n"
    "Always include a polite reply. Respond with ONLY valid JSON."
)

SCHEMA = {"action": "reply | task", "reply": "string", "task_summary": "string or null",
          "department": "housekeeping | frontdesk | concierge | valet | maintenance | null"}


def call_routing(message: str) -> str | None:
    info_lines = "\n".join([f"- {k}: {v}" for k, v in HOTEL_INFO.items()])
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "instructions": INSTRUCTIONS,
                "input": f"Schema: {SCHEMA}\nHotel info:\n{info_lines}\n\nMessage: {message}",
                "temperature": 0.0,
                "max_output_tokens": 120,
            },
            timeout=20,
        )
        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "").strip()
                        try:
                            return json.loads(text).get("action")
                        except Exception:
                            return None
    except Exception as e:
        print(f"  API error: {e}")
    return None


def suggest_improvements(failures: list[dict]) -> str:
    if not failures:
        return "No failures to improve on."
    failure_lines = "\n".join([
        f"- Message: \"{f['message']}\" | Expected: {f['expected']} | Got: {f['got']} | Category: {f['category']} | Notes: {f['notes']}"
        for f in failures
    ])
    prompt = (
        f"You are an expert prompt engineer. The following hotel concierge routing prompt is misclassifying some guest messages.\n\n"
        f"CURRENT PROMPT:\n{INSTRUCTIONS}\n\n"
        f"FAILURES ({len(failures)} cases):\n{failure_lines}\n\n"
        f"Analyze the failure patterns and suggest specific, minimal changes to the prompt to fix them. "
        f"Be concrete — show the exact text to add or change. Do not rewrite the entire prompt."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "instructions": "You are an expert prompt engineer. Be specific and concise.",
                "input": prompt,
                "temperature": 0.3,
                "max_output_tokens": 600,
            },
            timeout=30,
        )
        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content.get("text", "").strip()
    except Exception as e:
        return f"Error generating suggestions: {e}"
    return "No suggestions generated."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suggest", action="store_true", help="Generate prompt improvement suggestions for failures")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: OPENAI_API_KEY not set in .env")
        return

    print(f"Running {len(TEST_CASES)} test cases against {MODEL}...\n")

    results = {"pass": 0, "fail": 0}
    failures = []
    category_stats: dict[str, dict] = {}

    for i, (message, expected, category, notes) in enumerate(TEST_CASES, 1):
        got = call_routing(message)
        passed = got == expected

        if category not in category_stats:
            category_stats[category] = {"pass": 0, "fail": 0}

        if passed:
            results["pass"] += 1
            category_stats[category]["pass"] += 1
            print(f"  [{i:3}] ✓  {message[:60]}")
        else:
            results["fail"] += 1
            category_stats[category]["fail"] += 1
            failures.append({"message": message, "expected": expected, "got": got,
                              "category": category, "notes": notes})
            print(f"  [{i:3}] ✗  {message[:60]}")
            print(f"         Expected: {expected} | Got: {got} | {notes}")

    total = results["pass"] + results["fail"]
    pct = round(results["pass"] / total * 100)

    print(f"\n{'─'*60}")
    print(f"  RESULT: {results['pass']}/{total} passed  ({pct}% accuracy)")
    print(f"{'─'*60}")

    print("\n  By category:")
    for cat, stats in sorted(category_stats.items()):
        cat_total = stats["pass"] + stats["fail"]
        cat_pct = round(stats["pass"] / cat_total * 100)
        bar = "█" * (cat_pct // 5) + "░" * (20 - cat_pct // 5)
        print(f"    {cat:<15} {bar}  {stats['pass']}/{cat_total} ({cat_pct}%)")

    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for f in failures:
            print(f"    • [{f['category']}] \"{f['message']}\"")
            print(f"      Expected {f['expected']}, got {f['got']} — {f['notes']}")

    if args.suggest and failures:
        print(f"\n{'─'*60}")
        print("  PROMPT IMPROVEMENT SUGGESTIONS")
        print(f"{'─'*60}")
        suggestions = suggest_improvements(failures)
        print(suggestions)
    elif failures and not args.suggest:
        print(f"\n  Tip: run with --suggest to get AI-generated prompt improvements for the {len(failures)} failures.")

    print()


if __name__ == "__main__":
    main()
