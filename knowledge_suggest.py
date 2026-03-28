"""
knowledge_suggest.py — Self-expanding knowledge base helper.

After a staff member replies to a guest, this module uses the LLM to decide
whether the (question, answer) pair is worth adding to the knowledge base.
If yes, it drafts a clean title + content and creates a pending suggestion
for a manager to approve or dismiss.
"""

import json
import logging
import os
import threading

logger = logging.getLogger("hotel-concierge")

_SYSTEM_PROMPT = """You are a hotel operations assistant helping build a knowledge base.

Given a guest question and a staff reply, decide if the exchange contains useful,
reusable information that should be added to the hotel's knowledge base.

Respond ONLY with valid JSON — no markdown, no explanation:
{
  "worth_adding": true | false,
  "suggested_title": "Short title (3-8 words)",
  "suggested_content": "Polished, helpful paragraph for future guests."
}

Guidelines:
- worth_adding = true only if the answer is factual, specific, and reusable by other guests.
- Skip trivial exchanges ("thank you", "ok", etc.).
- Skip one-off personal requests that don't generalise.
- The suggested_content should be written for guests, not internal staff.
"""


def _call_openai(guest_question: str, staff_answer: str) -> dict | None:
    """Call the OpenAI API and return parsed JSON, or None on failure."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    if not api_key:
        return None
    try:
        import requests as _requests
        user_msg = f"Guest asked: {guest_question}\n\nStaff replied: {staff_answer}"
        resp = _requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": _SYSTEM_PROMPT,
                "input": user_msg,
                "temperature": 0.2,
                "max_output_tokens": 200,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.warning("knowledge_suggest_api_error", extra={"status": resp.status_code})
            return None
        data = resp.json()
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        raw = content.get("text", "").strip()
                        if raw:
                            return json.loads(raw)
        return None
    except Exception as exc:
        logger.warning("knowledge_suggest_llm_error", extra={"error": str(exc)})
        return None


def maybe_suggest_knowledge_entry(
    hotel_id: int,
    stay_id: int,
    guest_question: str,
    staff_answer: str,
    app_context,
) -> None:
    """
    Run in a background thread. Calls LLM, and if the exchange is worth adding,
    creates a pending knowledge suggestion in the DB.

    `app_context` should be a pushed Flask app context (use app.app_context()).
    """

    def _run():
        try:
            # Skip very short answers that are clearly not informative
            if len(staff_answer.strip()) < 15:
                return

            result = _call_openai(guest_question, staff_answer)
            if not result or not result.get("worth_adding"):
                return

            suggested_title = (result.get("suggested_title") or "").strip()
            suggested_content = (result.get("suggested_content") or "").strip()
            if not suggested_title or not suggested_content:
                return

            from db import create_knowledge_suggestion
            create_knowledge_suggestion(
                hotel_id=hotel_id,
                stay_id=stay_id,
                guest_question=guest_question,
                staff_answer=staff_answer,
                suggested_title=suggested_title,
                suggested_content=suggested_content,
            )
            logger.info(
                "knowledge_suggestion_created",
                extra={"hotel_id": hotel_id, "stay_id": stay_id, "title": suggested_title},
            )
        except Exception as exc:
            logger.exception("knowledge_suggest_error", extra={"error": str(exc)})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
